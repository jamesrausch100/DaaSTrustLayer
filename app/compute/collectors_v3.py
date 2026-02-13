"""
Market2Agent — Layer A: Data Collectors
The "credit bureaus" of the internet.

Every collector returns raw facts. No scoring logic. No opinions.
Just verifiable data from external sources.

Sources:
    1. Tranco Top 1M (popularity rank — bulk, from Redis)
    2. crt.sh (certificate transparency — free API)
    3. VirusTotal (70-vendor security consensus — API key)
    4. DNS records (SPF, DMARC, DKIM, DNSSEC, MX)
    5. HTTP headers (security headers scan)
    6. WHOIS (domain registration)
    7. Knowledge graph (Wikipedia, Wikidata, Crunchbase)
    8. Web presence (structured data, status page, docs)
"""
import asyncio
import re
import ssl
import socket
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from urllib.parse import urlparse

import httpx
import structlog

logger = structlog.get_logger()

# Timeout for all external calls
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


# ── Entity Parsing ────────────────────────────────

def parse_target(target: str) -> Dict[str, str]:
    """Parse any input into domain + name."""
    target = target.strip().lower()
    result = {"raw": target, "domain": "", "name": ""}

    # URL
    if target.startswith(("http://", "https://")):
        parsed = urlparse(target)
        result["domain"] = parsed.netloc.replace("www.", "")
        result["name"] = result["domain"].split(".")[0].capitalize()
        return result

    # Email
    if "@" in target and "." in target.split("@")[-1]:
        result["domain"] = target.split("@")[-1]
        result["name"] = result["domain"].split(".")[0].capitalize()
        return result

    # Domain (has dot, no spaces)
    if "." in target and " " not in target:
        result["domain"] = target.replace("www.", "")
        result["name"] = result["domain"].split(".")[0].capitalize()
        return result

    # Name — guess domain
    slug = re.sub(r'[^a-z0-9]', '', target)
    result["name"] = target.title()
    if len(slug) > 2:
        result["domain"] = f"{slug}.com"
    return result


# ── 1. Tranco ─────────────────────────────────────

async def collect_tranco(domain: str) -> Dict[str, Any]:
    """Look up domain rank from local Redis cache of Tranco Top 1M."""
    signals = {"tranco_rank": 0}
    try:
        import redis
        r = redis.Redis(host="redis", port=6379, db=1, decode_responses=True)
        rank = r.zscore("tranco", domain)
        if rank is not None:
            signals["tranco_rank"] = int(rank)
    except Exception as e:
        logger.debug("tranco_lookup_failed", error=str(e))
    return signals


# ── 2. crt.sh (Certificate Transparency) ─────────

async def collect_crtsh(domain: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """Query Certificate Transparency logs. Free, no API key."""
    signals = {
        "ssl_valid": False,
        "ssl_cert_type": "",
        "ssl_org": "",
        "first_cert_days_ago": 0,
        "total_certs_issued": 0,
        "cert_issuer": "",
    }
    try:
        resp = await client.get(
            f"https://crt.sh/?q={domain}&output=json",
            timeout=12.0,
        )
        if resp.status_code == 200:
            certs = resp.json()
            if certs:
                signals["ssl_valid"] = True
                signals["total_certs_issued"] = len(certs)

                # Find earliest cert
                earliest = None
                latest_issuer = ""
                for cert in certs:
                    entry_date = cert.get("entry_timestamp", "")
                    issuer = cert.get("issuer_name", "")
                    if entry_date:
                        try:
                            dt = datetime.fromisoformat(entry_date.replace("T", " ").split(".")[0])
                            if earliest is None or dt < earliest:
                                earliest = dt
                        except (ValueError, TypeError):
                            pass
                    if issuer:
                        latest_issuer = issuer

                if earliest:
                    age = (datetime.now() - earliest).days
                    signals["first_cert_days_ago"] = age

                signals["cert_issuer"] = latest_issuer

                # Detect EV/OV from issuer name patterns
                if latest_issuer:
                    il = latest_issuer.lower()
                    if "extended validation" in il or "ev " in il:
                        signals["ssl_cert_type"] = "EV"
                    elif "organization" in il or "ov " in il:
                        signals["ssl_cert_type"] = "OV"
                    else:
                        signals["ssl_cert_type"] = "DV"

    except Exception as e:
        logger.debug("crtsh_failed", domain=domain, error=str(e))

    # Direct SSL connection for org info
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
                signals["ssl_valid"] = True
                subject = dict(x[0] for x in cert.get("subject", []))
                org = subject.get("organizationName", "")
                if org and len(org) > 2:
                    signals["ssl_org"] = org
                    if not signals["ssl_cert_type"]:
                        signals["ssl_cert_type"] = "OV"
    except Exception:
        pass

    return signals


# ── 3. VirusTotal ─────────────────────────────────

async def collect_virustotal(domain: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """Query VirusTotal for 70-vendor security consensus."""
    signals = {
        "vt_malicious_count": 0,
        "vt_suspicious_count": 0,
        "vt_clean_count": 0,
        "vt_community_score": 0,
        "vt_categories": [],
        "vt_queried": False,
    }

    import os
    api_key = os.environ.get("VIRUSTOTAL_API_KEY", "")
    if not api_key:
        logger.debug("virustotal_no_api_key")
        return signals

    try:
        resp = await client.get(
            f"https://www.virustotal.com/api/v3/domains/{domain}",
            headers={"x-apikey": api_key},
            timeout=10.0,
        )
        signals["vt_queried"] = True

        if resp.status_code == 200:
            data = resp.json().get("data", {}).get("attributes", {})

            # Analysis stats
            stats = data.get("last_analysis_stats", {})
            signals["vt_malicious_count"] = stats.get("malicious", 0)
            signals["vt_suspicious_count"] = stats.get("suspicious", 0)
            signals["vt_clean_count"] = stats.get("undetected", 0) + stats.get("harmless", 0)

            # Community votes
            votes = data.get("total_votes", {})
            signals["vt_community_score"] = votes.get("harmless", 0) - votes.get("malicious", 0)

            # Categories
            cats = data.get("categories", {})
            signals["vt_categories"] = list(set(cats.values())) if cats else []

    except Exception as e:
        logger.debug("virustotal_failed", domain=domain, error=str(e))

    return signals


# ── 4. DNS Records ────────────────────────────────

async def collect_dns(domain: str) -> Dict[str, Any]:
    """Check DNS security configuration."""
    signals = {
        "dns_has_spf": False,
        "dns_has_dmarc": False,
        "dns_has_dkim": False,
        "dns_has_dnssec": False,
        "dns_has_mx": False,
    }

    try:
        import dns.resolver
        import dns.dnssec

        # SPF
        try:
            answers = dns.resolver.resolve(domain, "TXT")
            for rdata in answers:
                txt = str(rdata)
                if "v=spf1" in txt:
                    signals["dns_has_spf"] = True
        except Exception:
            pass

        # DMARC
        try:
            answers = dns.resolver.resolve(f"_dmarc.{domain}", "TXT")
            for rdata in answers:
                if "v=DMARC1" in str(rdata):
                    signals["dns_has_dmarc"] = True
        except Exception:
            pass

        # MX
        try:
            answers = dns.resolver.resolve(domain, "MX")
            if answers:
                signals["dns_has_mx"] = True
        except Exception:
            pass

        # DKIM (check common selectors)
        for selector in ["google", "default", "selector1", "mail", "k1"]:
            try:
                answers = dns.resolver.resolve(f"{selector}._domainkey.{domain}", "TXT")
                if answers:
                    signals["dns_has_dkim"] = True
                    break
            except Exception:
                continue

        # DNSSEC
        try:
            answers = dns.resolver.resolve(domain, "DNSKEY")
            if answers:
                signals["dns_has_dnssec"] = True
        except Exception:
            pass

    except ImportError:
        logger.debug("dnspython_not_installed")

    return signals


# ── 5. HTTP Security Headers ─────────────────────

async def collect_http_headers(domain: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """Scan HTTP response headers for security configuration."""
    signals = {
        "http_has_hsts": False,
        "http_has_csp": False,
        "http_has_xframe": False,
        "http_has_xcontent_type": False,
        "http_has_referrer_policy": False,
        "http_has_permissions_policy": False,
        "http_status": 0,
        "has_security_txt": False,
        "has_robots_txt": False,
    }

    try:
        resp = await client.get(f"https://{domain}", follow_redirects=True, timeout=10.0)
        signals["http_status"] = resp.status_code
        headers = {k.lower(): v for k, v in resp.headers.items()}

        signals["http_has_hsts"] = "strict-transport-security" in headers
        signals["http_has_csp"] = "content-security-policy" in headers
        signals["http_has_xframe"] = "x-frame-options" in headers
        signals["http_has_xcontent_type"] = "x-content-type-options" in headers
        signals["http_has_referrer_policy"] = "referrer-policy" in headers
        signals["http_has_permissions_policy"] = "permissions-policy" in headers

    except Exception as e:
        logger.debug("http_headers_failed", domain=domain, error=str(e))

    # security.txt
    try:
        resp = await client.get(f"https://{domain}/.well-known/security.txt", timeout=5.0)
        if resp.status_code == 200 and "contact" in resp.text.lower():
            signals["has_security_txt"] = True
    except Exception:
        pass

    # robots.txt
    try:
        resp = await client.get(f"https://{domain}/robots.txt", timeout=5.0)
        if resp.status_code == 200 and len(resp.text) > 10:
            signals["has_robots_txt"] = True
    except Exception:
        pass

    return signals


# ── 6. WHOIS ──────────────────────────────────────

async def collect_whois(domain: str) -> Dict[str, Any]:
    """WHOIS registration data."""
    signals = {
        "domain_age_days": 0,
        "whois_org": "",
        "whois_registrar": "",
        "domain_expiry_years_ahead": 0.0,
    }

    try:
        import whois
        w = whois.whois(domain)

        if w.creation_date:
            created = w.creation_date
            if isinstance(created, list):
                created = created[0]
            if isinstance(created, datetime):
                from datetime import timezone as tz; signals["domain_age_days"] = (datetime.now(tz.utc).replace(tzinfo=None) - created.replace(tzinfo=None)).days

        if w.expiration_date:
            exp = w.expiration_date
            if isinstance(exp, list):
                exp = exp[0]
            if isinstance(exp, datetime):
                years_ahead = (exp - datetime.now()).days / 365.25
                signals["domain_expiry_years_ahead"] = round(max(years_ahead, 0), 1)

        signals["whois_org"] = str(w.org or "")
        signals["whois_registrar"] = str(w.registrar or "")

    except ImportError:
        logger.debug("whois_not_installed")
    except Exception as e:
        logger.debug("whois_failed", domain=domain, error=str(e))

    return signals


# ── 7. Knowledge Graph ────────────────────────────

async def collect_knowledge_graph(name: str, domain: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """Wikipedia, Wikidata, Crunchbase."""
    signals = {
        "has_wikipedia": False,
        "has_wikidata": False,
        "has_crunchbase": False,
        "wikidata_employee_count": 0,
        "wikidata_founding_year": 0,
    }

    wiki_name = name.replace(" ", "_")

    # Wikipedia
    try:
        resp = await client.head(
            f"https://en.wikipedia.org/wiki/{wiki_name}",
            timeout=5.0, follow_redirects=True,
        )
        if resp.status_code == 200:
            signals["has_wikipedia"] = True
    except Exception:
        pass

    # Wikidata
    try:
        resp = await client.get(
            f"https://www.wikidata.org/w/api.php?action=wbsearchentities&search={name}&language=en&format=json&limit=1",
            timeout=5.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("search"):
                signals["has_wikidata"] = True
    except Exception:
        pass

    # Crunchbase
    try:
        slug = re.sub(r'[^a-z0-9-]', '', name.lower().replace(" ", "-"))
        resp = await client.head(
            f"https://www.crunchbase.com/organization/{slug}",
            timeout=5.0, follow_redirects=True,
        )
        if resp.status_code == 200:
            signals["has_crunchbase"] = True
    except Exception:
        pass

    return signals


# ── 8. Web Presence ───────────────────────────────

async def collect_web_presence(domain: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """Structured data, status page, API docs, changelog."""
    signals = {
        "has_structured_data": False,
        "has_org_schema": False,
        "has_status_page": False,
        "has_api_docs": False,
        "has_changelog": False,
    }

    try:
        resp = await client.get(f"https://{domain}", follow_redirects=True, timeout=10.0)
        if resp.status_code == 200:
            html = resp.text

            if "application/ld+json" in html:
                signals["has_structured_data"] = True
                if '"Organization"' in html or '"Corporation"' in html:
                    signals["has_org_schema"] = True

            for indicator, key in [
                ("/docs", "has_api_docs"),
                ("/api", "has_api_docs"),
                ("/developer", "has_api_docs"),
                ("/changelog", "has_changelog"),
            ]:
                if f'href="{indicator}' in html or f"href='{indicator}" in html:
                    signals[key] = True

    except Exception:
        pass

    # Status page
    for subdomain in [f"status.{domain}", f"{domain.split('.')[0]}.statuspage.io"]:
        try:
            resp = await client.head(f"https://{subdomain}", timeout=5.0, follow_redirects=True)
            if resp.status_code == 200:
                signals["has_status_page"] = True
                break
        except Exception:
            pass

    return signals


# ── 9. Social Presence ────────────────────────────

async def collect_social(name: str, domain: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """Check social media profile existence."""
    signals = {
        "social_twitter": False,
        "social_linkedin": False,
        "social_github": False,
        "social_facebook": False,
        "social_youtube": False,
        "social_instagram": False,
        "social_count": 0,
    }

    slug = domain.split(".")[0] if domain else re.sub(r'[^a-z0-9]', '', name.lower())

    checks = {
        "social_twitter": f"https://x.com/{slug}",
        "social_linkedin": f"https://www.linkedin.com/company/{slug}",
        "social_github": f"https://github.com/{slug}",
        "social_facebook": f"https://www.facebook.com/{slug}",
        "social_youtube": f"https://www.youtube.com/@{slug}",
        "social_instagram": f"https://www.instagram.com/{slug}",
    }

    async def check(key: str, url: str):
        try:
            resp = await client.head(url, timeout=5.0, follow_redirects=True)
            return key, resp.status_code == 200
        except Exception:
            return key, False

    results = await asyncio.gather(*[check(k, u) for k, u in checks.items()])
    for key, exists in results:
        if isinstance(key, str):
            signals[key] = exists
            if exists:
                signals["social_count"] += 1

    return signals


# ── Master Collector ──────────────────────────────

async def collect_all_signals(target: str) -> "RawSignals":
    """
    Run ALL collectors in parallel. Return a complete RawSignals object.
    This is Layer A — the data layer.
    """
    import time
    from app.trust.engine_v3 import RawSignals

    start = time.time()
    parsed = parse_target(target)
    domain = parsed["domain"]
    name = parsed["name"]

    raw = RawSignals(target=target)
    raw.sources_queried = ["tranco", "crtsh", "virustotal", "dns", "http", "whois", "knowledge", "web", "social"]

    async with httpx.AsyncClient(
        headers={"User-Agent": "Market2Agent TrustBot/3.0 (+https://market2agent.ai/bot)"},
        follow_redirects=True,
        verify=True,
        timeout=_TIMEOUT,
    ) as client:

        # Fire all collectors in parallel
        tasks = {}
        if domain:
            tasks["tranco"] = collect_tranco(domain)
            tasks["crtsh"] = collect_crtsh(domain, client)
            tasks["virustotal"] = collect_virustotal(domain, client)
            tasks["dns"] = collect_dns(domain)
            tasks["http"] = collect_http_headers(domain, client)
            tasks["whois"] = collect_whois(domain)
            tasks["web"] = collect_web_presence(domain, client)

        tasks["knowledge"] = collect_knowledge_graph(name, domain, client)
        tasks["social"] = collect_social(name, domain, client)

        # Execute all in parallel
        keys = list(tasks.keys())
        results_list = await asyncio.gather(
            *[_safe_collect(k, t) for k, t in tasks.items()],
            return_exceptions=True,
        )

        # Merge results
        results = {}
        for i, res in enumerate(results_list):
            if isinstance(res, tuple):
                cname, cdata = res
                results[cname] = cdata
                raw.sources_responded.append(cname)
            elif isinstance(res, Exception):
                raw.collection_errors.append(f"{keys[i]}: {str(res)[:100]}")

    # Map collector outputs → RawSignals fields
    tr = results.get("tranco", {})
    ct = results.get("crtsh", {})
    vt = results.get("virustotal", {})
    dn = results.get("dns", {})
    ht = results.get("http", {})
    wh = results.get("whois", {})
    kg = results.get("knowledge", {})
    wp = results.get("web", {})
    so = results.get("social", {})

    # Tranco
    raw.tranco_rank = tr.get("tranco_rank", 0)

    # crt.sh
    raw.ssl_valid = ct.get("ssl_valid", False)
    raw.ssl_cert_type = ct.get("ssl_cert_type", "")
    raw.ssl_org = ct.get("ssl_org", "")
    raw.first_cert_days_ago = ct.get("first_cert_days_ago", 0)
    raw.total_certs_issued = ct.get("total_certs_issued", 0)
    raw.cert_issuer = ct.get("cert_issuer", "")

    # VirusTotal
    raw.vt_malicious_count = vt.get("vt_malicious_count", 0)
    raw.vt_suspicious_count = vt.get("vt_suspicious_count", 0)
    raw.vt_clean_count = vt.get("vt_clean_count", 0)
    raw.vt_community_score = vt.get("vt_community_score", 0)
    raw.vt_categories = vt.get("vt_categories", [])
    raw.vt_queried = vt.get("vt_queried", False)

    # DNS
    raw.dns_has_spf = dn.get("dns_has_spf", False)
    raw.dns_has_dmarc = dn.get("dns_has_dmarc", False)
    raw.dns_has_dkim = dn.get("dns_has_dkim", False)
    raw.dns_has_dnssec = dn.get("dns_has_dnssec", False)
    raw.dns_has_mx = dn.get("dns_has_mx", False)

    # HTTP
    raw.http_has_hsts = ht.get("http_has_hsts", False)
    raw.http_has_csp = ht.get("http_has_csp", False)
    raw.http_has_xframe = ht.get("http_has_xframe", False)
    raw.http_has_xcontent_type = ht.get("http_has_xcontent_type", False)
    raw.http_has_referrer_policy = ht.get("http_has_referrer_policy", False)
    raw.http_has_permissions_policy = ht.get("http_has_permissions_policy", False)
    raw.http_status = ht.get("http_status", 0)
    raw.has_security_txt = ht.get("has_security_txt", False)
    raw.has_robots_txt = ht.get("has_robots_txt", False)

    # WHOIS
    raw.domain_age_days = wh.get("domain_age_days", 0)
    raw.whois_org = wh.get("whois_org", "")
    raw.whois_registrar = wh.get("whois_registrar", "")
    raw.domain_expiry_years_ahead = wh.get("domain_expiry_years_ahead", 0.0)

    # Knowledge graph
    raw.has_wikipedia = kg.get("has_wikipedia", False)
    raw.has_wikidata = kg.get("has_wikidata", False)
    raw.has_crunchbase = kg.get("has_crunchbase", False)

    # Web presence
    raw.has_structured_data = wp.get("has_structured_data", False)
    raw.has_org_schema = wp.get("has_org_schema", False)
    raw.has_status_page = wp.get("has_status_page", False)
    raw.has_api_docs = wp.get("has_api_docs", False)
    raw.has_changelog = wp.get("has_changelog", False)

    # Social
    raw.social_twitter = so.get("social_twitter", False)
    raw.social_linkedin = so.get("social_linkedin", False)
    raw.social_github = so.get("social_github", False)
    raw.social_facebook = so.get("social_facebook", False)
    raw.social_youtube = so.get("social_youtube", False)
    raw.social_instagram = so.get("social_instagram", False)
    raw.social_count = so.get("social_count", 0)

    raw.collection_time_ms = round((time.time() - start) * 1000, 2)
    return raw


async def _safe_collect(name: str, coro):
    """Run a collector, return (name, result) or (name, {}) on failure."""
    try:
        result = await asyncio.wait_for(coro, timeout=15.0)
        return name, result
    except Exception as e:
        logger.debug("collector_failed", name=name, error=str(e))
        return name, {}
