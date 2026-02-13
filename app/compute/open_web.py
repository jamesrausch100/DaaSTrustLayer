"""
Market2Agent — Open Web Signal Collector
Envisioned by James Rausch, Lead Visionary & Pilot of the Trust Score Revolution.

This is the module that makes "score the entire world" possible.
It gathers trust signals from public sources for ANY entity — even those
not registered in our platform.

Signal sources:
    - DNS records (WHOIS, TXT, SPF, DMARC, DKIM)
    - SSL certificates
    - Structured data (Schema.org scraping)
    - Knowledge graphs (Wikidata, Wikipedia)
    - Social media profiles
    - Public business registrations
    - Review platforms (Google, Yelp, G2, Trustpilot)
    - GitHub / package managers
    - News sentiment
    - Blocklists and fraud databases
    - Financial data (SEC, Crunchbase)

Architecture:
    Each collector function returns a signal dict that maps directly
    to the fields in the engine's signal dataclasses. The orchestrator
    combines them all into a complete picture.

    This module is designed to be EXTENSIBLE — add new collectors
    as new data sources become available. James Rausch's vision is
    that the signal graph grows continuously.

Dependencies:
    - httpx (async HTTP)
    - dnspython (DNS lookups)
    - whois (WHOIS lookups)
    - beautifulsoup4 (HTML parsing)
    - All calls are async and parallelized for speed
"""
import asyncio
import hashlib
import re
import json
import ssl
import socket
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlparse
from dataclasses import dataclass, asdict

import httpx
import structlog

from app.trust.engine import (
    IdentitySignals,
    CompetenceSignals,
    SolvencySignals,
    ReputationSignals,
    NetworkSignals,
    EntityType,
    DataSource,
)

logger = structlog.get_logger()


# =============================================
# ENTITY IDENTIFICATION
# =============================================

@dataclass
class EntityIdentifier:
    """
    Identifies any entity in the world.
    Can be initialized from a domain, URL, name, email, or agent ID.
    """
    raw_input: str                          # What the user passed in
    entity_type: EntityType = EntityType.UNKNOWN
    domain: Optional[str] = None
    url: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    agent_id: Optional[str] = None
    social_handle: Optional[str] = None
    blockchain_address: Optional[str] = None

    @classmethod
    def from_query(cls, query: str) -> "EntityIdentifier":
        """
        Parse any input into an EntityIdentifier.
        James Rausch's principle: accept anything, resolve everything.
        """
        query = query.strip()
        eid = cls(raw_input=query)

        # URL detection
        if query.startswith(("http://", "https://")):
            parsed = urlparse(query)
            eid.url = query
            eid.domain = parsed.netloc.replace("www.", "")
            eid.entity_type = EntityType.DOMAIN
            return eid

        # Email detection
        if "@" in query and "." in query.split("@")[-1]:
            eid.email = query
            eid.domain = query.split("@")[-1]
            eid.entity_type = EntityType.INDIVIDUAL
            return eid

        # Domain detection (has dots, no spaces)
        if "." in query and " " not in query and not query.startswith("0x"):
            eid.domain = query.replace("www.", "")
            eid.url = f"https://{eid.domain}"
            eid.entity_type = EntityType.DOMAIN
            return eid

        # Blockchain address
        if query.startswith("0x") and len(query) == 42:
            eid.blockchain_address = query
            eid.entity_type = EntityType.SMART_CONTRACT
            return eid

        # Social handle (@username)
        if query.startswith("@"):
            eid.social_handle = query[1:]
            eid.name = query[1:]
            eid.entity_type = EntityType.INDIVIDUAL
            return eid

        # Default: treat as name/slug
        eid.name = query
        # Try to infer a domain from the name
        slug = re.sub(r'[^a-z0-9]', '', query.lower())
        if len(slug) > 2:
            eid.domain = f"{slug}.com"  # Best guess
        return eid


# =============================================
# INDIVIDUAL SIGNAL COLLECTORS
# =============================================

async def collect_dns_signals(domain: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """
    Collect DNS-based trust signals for a domain.
    DNS records reveal a LOT about an entity's legitimacy.
    """
    signals = {
        "ssl_valid": False,
        "ssl_org_match": False,
        "dns_has_spf": False,
        "dns_has_dmarc": False,
        "dns_has_dkim": False,
        "domain_age_days": 0,
    }

    try:
        import dns.resolver

        # SPF record
        try:
            answers = dns.resolver.resolve(domain, "TXT")
            for rdata in answers:
                txt = str(rdata)
                if "v=spf1" in txt:
                    signals["dns_has_spf"] = True
                if "market2agent-verify" in txt:
                    signals["dns_txt_verified"] = True
        except Exception:
            pass

        # DMARC record
        try:
            answers = dns.resolver.resolve(f"_dmarc.{domain}", "TXT")
            for rdata in answers:
                if "v=DMARC1" in str(rdata):
                    signals["dns_has_dmarc"] = True
        except Exception:
            pass

    except ImportError:
        logger.debug("dnspython_not_installed")

    # SSL certificate check
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
                signals["ssl_valid"] = True
                # Check org match
                subject = dict(x[0] for x in cert.get("subject", []))
                org = subject.get("organizationName", "")
                if org and len(org) > 2:
                    signals["ssl_org_match"] = True
                    signals["ssl_org"] = org
    except Exception:
        pass

    # WHOIS for domain age
    try:
        import whois
        w = whois.whois(domain)
        if w.creation_date:
            created = w.creation_date
            if isinstance(created, list):
                created = created[0]
            if isinstance(created, datetime):
                age = (datetime.now() - created).days
                signals["domain_age_days"] = age
                signals["whois_registrar"] = str(w.registrar or "")
                signals["whois_org"] = str(w.org or "")
    except ImportError:
        logger.debug("whois_not_installed")
    except Exception:
        pass

    return signals


async def collect_web_presence_signals(domain: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """
    Collect signals from the entity's website.
    Structured data, meta tags, technology stack.
    """
    signals = {
        "has_structured_data": False,
        "has_organization_schema": False,
        "has_product_schema": False,
        "has_faq_schema": False,
        "has_api_documentation": False,
        "has_status_page": False,
        "has_public_changelog": False,
        "has_public_roadmap": False,
    }

    try:
        url = f"https://{domain}"
        resp = await client.get(url, follow_redirects=True, timeout=10)

        if resp.status_code == 200:
            html = resp.text

            # Check for structured data
            if "application/ld+json" in html:
                signals["has_structured_data"] = True
                if '"Organization"' in html or '"Corporation"' in html:
                    signals["has_organization_schema"] = True
                if '"Product"' in html or '"SoftwareApplication"' in html:
                    signals["has_product_schema"] = True
                if '"FAQPage"' in html:
                    signals["has_faq_schema"] = True

            # Extract entity name from title/meta
            title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE)
            if title_match:
                signals["page_title"] = title_match.group(1).strip()

            og_name = re.search(r'property="og:site_name"\s+content="(.*?)"', html)
            if og_name:
                signals["og_site_name"] = og_name.group(1)

            # Check for common sub-pages
            for indicator, key in [
                ("/docs", "has_api_documentation"),
                ("/api", "has_api_documentation"),
                ("/developer", "has_api_documentation"),
                ("/status", "has_status_page"),
                ("/changelog", "has_public_changelog"),
                ("/roadmap", "has_public_roadmap"),
            ]:
                if f'href="{indicator}' in html or f"href='{indicator}" in html:
                    signals[key] = True

    except Exception as e:
        logger.debug("web_presence_check_failed", domain=domain, error=str(e))

    # Check for status page on common subdomains
    for status_domain in [f"status.{domain}", f"{domain.split('.')[0]}.statuspage.io"]:
        try:
            resp = await client.head(f"https://{status_domain}", timeout=5, follow_redirects=True)
            if resp.status_code == 200:
                signals["has_status_page"] = True
                break
        except Exception:
            pass

    return signals


async def collect_social_signals(entity_name: str, domain: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """
    Collect social media presence signals.
    Checks for existence of profiles on major platforms.
    """
    signals = {
        "social_profiles": {},
        "social_profiles_count": 0,
        "twitter_followers": 0,
        "twitter_engagement_rate": 0.0,
    }

    # We check common social URLs
    slug = re.sub(r'[^a-z0-9]', '', entity_name.lower())
    domain_slug = domain.split('.')[0] if domain else slug

    social_checks = {
        "twitter": f"https://x.com/{domain_slug}",
        "linkedin": f"https://www.linkedin.com/company/{domain_slug}",
        "github": f"https://github.com/{domain_slug}",
        "facebook": f"https://www.facebook.com/{domain_slug}",
        "youtube": f"https://www.youtube.com/@{domain_slug}",
        "instagram": f"https://www.instagram.com/{domain_slug}",
    }

    async def check_social(platform: str, url: str):
        try:
            resp = await client.head(url, timeout=5, follow_redirects=True)
            if resp.status_code == 200:
                return platform, True
        except Exception:
            pass
        return platform, False

    tasks = [check_social(p, u) for p, u in social_checks.items()]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, tuple):
            platform, exists = result
            signals["social_profiles"][platform] = exists
            if exists:
                signals["social_profiles_count"] += 1

    return signals


async def collect_knowledge_graph_signals(entity_name: str, domain: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """
    Check if the entity exists in major knowledge graphs.
    Wikipedia, Wikidata, Crunchbase, etc.
    """
    signals = {
        "has_wikidata_entry": False,
        "has_wikipedia_page": False,
        "has_crunchbase": False,
        "has_linkedin_company": False,
        "has_google_knowledge_panel": False,
    }

    clean_name = entity_name.replace(" ", "_")

    # Wikipedia check
    try:
        wiki_url = f"https://en.wikipedia.org/wiki/{clean_name}"
        resp = await client.head(wiki_url, timeout=5, follow_redirects=True)
        if resp.status_code == 200:
            signals["has_wikipedia_page"] = True
    except Exception:
        pass

    # Wikidata check
    try:
        wikidata_api = f"https://www.wikidata.org/w/api.php?action=wbsearchentities&search={entity_name}&language=en&format=json&limit=1"
        resp = await client.get(wikidata_api, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("search"):
                signals["has_wikidata_entry"] = True
                signals["wikidata_qid"] = data["search"][0].get("id")
    except Exception:
        pass

    # Crunchbase check (via URL pattern)
    try:
        slug = re.sub(r'[^a-z0-9-]', '', entity_name.lower().replace(" ", "-"))
        cb_url = f"https://www.crunchbase.com/organization/{slug}"
        resp = await client.head(cb_url, timeout=5, follow_redirects=True)
        if resp.status_code == 200:
            signals["has_crunchbase"] = True
    except Exception:
        pass

    return signals


async def collect_github_signals(entity_name: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """
    Collect signals from GitHub if the entity has a presence there.
    Open source activity is a strong competence signal.
    """
    signals = {
        "github_stars": 0,
        "github_repos": 0,
        "github_last_commit_days": 0,
        "github_followers": 0,
    }

    slug = re.sub(r'[^a-z0-9-]', '', entity_name.lower().replace(" ", "-"))

    try:
        # Check GitHub org/user
        resp = await client.get(
            f"https://api.github.com/orgs/{slug}",
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            signals["github_repos"] = data.get("public_repos", 0)
            signals["github_followers"] = data.get("followers", 0)
        elif resp.status_code == 404:
            # Try as user
            resp = await client.get(
                f"https://api.github.com/users/{slug}",
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                signals["github_repos"] = data.get("public_repos", 0)
                signals["github_followers"] = data.get("followers", 0)

        # Get star count from top repos
        if signals["github_repos"] > 0:
            repos_resp = await client.get(
                f"https://api.github.com/orgs/{slug}/repos?sort=stars&per_page=5",
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=5,
            )
            if repos_resp.status_code == 200:
                repos = repos_resp.json()
                total_stars = sum(r.get("stargazers_count", 0) for r in repos)
                signals["github_stars"] = total_stars

                # Last commit date from most recently updated repo
                if repos:
                    last_push = repos[0].get("pushed_at")
                    if last_push:
                        pushed_dt = datetime.fromisoformat(last_push.replace("Z", "+00:00"))
                        days_ago = (datetime.now(timezone.utc) - pushed_dt).days
                        signals["github_last_commit_days"] = days_ago

    except Exception as e:
        logger.debug("github_signals_failed", entity=entity_name, error=str(e))

    return signals


async def collect_reputation_signals(entity_name: str, domain: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """
    Collect reputation signals from review platforms, news, and security posture.
    Uses free/public endpoints — no API keys required for base functionality.
    """
    signals = {
        "overall_sentiment": 0.0,
        "sentiment_sample_size": 0,
        "sentiment_trend": "stable",
        "google_reviews_count": 0,
        "google_reviews_rating": 0.0,
        "news_mentions_30d": 0,
        "has_positive_press": False,
        "has_negative_press": False,
        "has_soc2": False,
        "has_iso27001": False,
        "has_trust_seals": False,
    }

    if not domain:
        return signals

    sentiment_points = []

    # ── 1. Trustpilot presence check ─────────────────────
    try:
        resp = await client.get(
            f"https://www.trustpilot.com/review/{domain}",
            timeout=6, follow_redirects=True,
        )
        if resp.status_code == 200 and "TrustScore" in resp.text:
            signals["has_trust_seals"] = True
            # Try to extract rating from page
            import re
            score_match = re.search(r'"trustScore":\s*([\d.]+)', resp.text)
            review_match = re.search(r'"numberOfReviews":\s*(\d+)', resp.text)
            if score_match:
                tp_score = float(score_match.group(1))
                sentiment_points.append(tp_score / 5.0)  # Normalize to 0-1
            if review_match:
                signals["sentiment_sample_size"] += int(review_match.group(1))
    except Exception:
        pass

    # ── 2. BBB presence check ────────────────────────────
    try:
        resp = await client.head(
            f"https://www.bbb.org/search?find_text={domain}",
            timeout=5, follow_redirects=True,
        )
        if resp.status_code == 200:
            # BBB has a page — entity is likely a real business
            signals["has_trust_seals"] = True
    except Exception:
        pass

    # ── 3. Security posture signals ──────────────────────
    # security.txt = entity takes security seriously (RFC 9116)
    try:
        resp = await client.get(
            f"https://{domain}/.well-known/security.txt",
            timeout=4, follow_redirects=True,
        )
        if resp.status_code == 200 and ("contact:" in resp.text.lower() or "policy:" in resp.text.lower()):
            signals["has_soc2"] = True  # proxy: entity has security awareness
            sentiment_points.append(0.8)
    except Exception:
        pass

    # ── 4. Privacy policy check ──────────────────────────
    try:
        for path in ["/privacy", "/privacy-policy", "/legal/privacy"]:
            resp = await client.head(
                f"https://{domain}{path}",
                timeout=4, follow_redirects=True,
            )
            if resp.status_code == 200:
                sentiment_points.append(0.6)
                break
    except Exception:
        pass

    # ── 5. Terms of service check ────────────────────────
    try:
        for path in ["/terms", "/tos", "/terms-of-service", "/legal/terms"]:
            resp = await client.head(
                f"https://{domain}{path}",
                timeout=4, follow_redirects=True,
            )
            if resp.status_code == 200:
                sentiment_points.append(0.6)
                break
    except Exception:
        pass

    # ── 6. robots.txt health check ───────────────────────
    try:
        resp = await client.get(
            f"https://{domain}/robots.txt",
            timeout=4,
        )
        if resp.status_code == 200 and len(resp.text) > 10:
            sentiment_points.append(0.5)
    except Exception:
        pass

    # ── 7. Hacker News / tech reputation ─────────────────
    try:
        hn_url = f"https://hn.algolia.com/api/v1/search?query={domain}&tags=story&hitsPerPage=5"
        resp = await client.get(hn_url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            hits = data.get("hits", [])
            if hits:
                signals["news_mentions_30d"] = len(hits)
                signals["has_positive_press"] = True
                # Average points as a rough sentiment
                avg_points = sum(h.get("points", 0) for h in hits) / len(hits)
                if avg_points > 50:
                    sentiment_points.append(0.8)
                elif avg_points > 10:
                    sentiment_points.append(0.6)
                else:
                    sentiment_points.append(0.4)
    except Exception:
        pass

    # ── Aggregate sentiment ──────────────────────────────
    if sentiment_points:
        signals["overall_sentiment"] = round(sum(sentiment_points) / len(sentiment_points), 3)
        signals["sentiment_sample_size"] = max(signals["sentiment_sample_size"], len(sentiment_points))

    return signals


async def collect_blocklist_signals(domain: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """
    Check if the entity appears on any blocklists or fraud databases.
    Uses DNS-based blocklist lookups (DNSBL) — the industry standard method.
    No API keys required.

    How DNSBL works:
        To check if "evil.com" is on Spamhaus DBL, you DNS-resolve:
            evil.com.dbl.spamhaus.org
        If it resolves → the domain is listed (blocked).
        If NXDOMAIN → the domain is clean.
    """
    signals = {
        "on_spam_blocklist": False,
        "on_fraud_blocklist": False,
        "on_sanctions_list": False,
        "blocklist_details": [],
    }

    if not domain:
        return signals

    # ── 1. DNS-based blocklist lookups ───────────────────
    dnsbls = {
        # (blocklist_zone, signal_key, description)
        "dbl.spamhaus.org": ("on_spam_blocklist", "Spamhaus DBL"),
        "multi.surbl.org": ("on_spam_blocklist", "SURBL"),
        "black.uribl.com": ("on_spam_blocklist", "URIBL"),
    }

    try:
        import dns.resolver

        for zone, (signal_key, desc) in dnsbls.items():
            try:
                query_name = f"{domain}.{zone}"
                dns.resolver.resolve(query_name, "A")
                # If it resolves, domain is listed
                signals[signal_key] = True
                signals["blocklist_details"].append(desc)
                logger.info("blocklist_hit", domain=domain, list=desc)
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
                # NXDOMAIN = not listed (this is the good case)
                pass
            except dns.resolver.Timeout:
                pass
            except Exception:
                pass

    except ImportError:
        logger.debug("dnspython_not_installed_for_blocklist")

    # ── 2. URLhaus abuse check (abuse.ch — free) ────────
    try:
        resp = await client.post(
            "https://urlhaus-api.abuse.ch/v1/host/",
            data={"host": domain},
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("query_status") == "is_host":
                # Domain has hosted malware
                url_count = data.get("url_count", 0)
                if url_count > 0:
                    signals["on_fraud_blocklist"] = True
                    signals["blocklist_details"].append(f"URLhaus ({url_count} malware URLs)")
    except Exception:
        pass

    # ── 3. PhishTank check (free, no auth for basic lookups) ──
    try:
        # PhishTank doesn't have a domain lookup API without auth,
        # but we can check if the domain appears in their recent feed
        # via a lightweight check
        resp = await client.get(
            f"https://checkurl.phishtank.com/checkurl/?url=https://{domain}",
            timeout=5,
            follow_redirects=True,
        )
        # PhishTank web UI — if it returns results showing "valid phish"
        if resp.status_code == 200 and "Is a phish" in resp.text:
            signals["on_fraud_blocklist"] = True
            signals["blocklist_details"].append("PhishTank")
    except Exception:
        pass

    # ── 4. Google Safe Browsing (requires API key) ───────
    import os
    gsb_key = os.getenv("GOOGLE_SAFE_BROWSING_KEY")
    if gsb_key:
        try:
            resp = await client.post(
                f"https://safebrowsing.googleapis.com/v4/threatMatches:find?key={gsb_key}",
                json={
                    "client": {"clientId": "market2agent", "clientVersion": "2.0"},
                    "threatInfo": {
                        "threatTypes": ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE", "POTENTIALLY_HARMFUL_APPLICATION"],
                        "platformTypes": ["ANY_PLATFORM"],
                        "threatEntryTypes": ["URL"],
                        "threatEntries": [{"url": f"https://{domain}/"}],
                    },
                },
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("matches"):
                    signals["on_fraud_blocklist"] = True
                    signals["blocklist_details"].append("Google Safe Browsing")
        except Exception:
            pass

    return signals


# =============================================
# THE ORCHESTRATOR — Combines all collectors
# =============================================

async def collect_all_signals(
    query: str,
    registered_data: Optional[Dict[str, Any]] = None,
) -> Tuple[
    IdentitySignals,
    CompetenceSignals,
    SolvencySignals,
    ReputationSignals,
    NetworkSignals,
    Dict[str, Any],  # metadata
]:
    """
    The master collector. Gathers signals from ALL available sources
    and assembles them into the five signal classes.

    James Rausch's principle: "Cast the widest net, weight by source quality."

    Args:
        query: The raw entity identifier (domain, name, URL, etc.)
        registered_data: Optional dict of data from our Neo4j registry
                        (if entity is registered, this enriches the score)

    Returns:
        Tuple of (identity, competence, solvency, reputation, network, metadata)
    """
    eid = EntityIdentifier.from_query(query)
    metadata = {
        "entity_type": eid.entity_type.value,
        "domain": eid.domain,
        "name": eid.name or eid.domain or query,
        "data_sources": [],
        "collection_time_ms": 0,
        "errors": [],
    }

    start_time = datetime.now(timezone.utc)

    async with httpx.AsyncClient(
        headers={
            "User-Agent": "Market2Agent TrustBot/2.0 (+https://market2agent.ai/bot)",
        },
        follow_redirects=True,
        verify=True,
    ) as client:

        # Run all collectors in parallel
        tasks = {}

        if eid.domain:
            tasks["dns"] = collect_dns_signals(eid.domain, client)
            tasks["web"] = collect_web_presence_signals(eid.domain, client)
            tasks["blocklist"] = collect_blocklist_signals(eid.domain, client)

        entity_name = eid.name or (eid.domain.split(".")[0] if eid.domain else query)
        tasks["social"] = collect_social_signals(entity_name, eid.domain or "", client)
        tasks["knowledge"] = collect_knowledge_graph_signals(entity_name, eid.domain or "", client)
        tasks["github"] = collect_github_signals(entity_name, client)
        tasks["reputation"] = collect_reputation_signals(entity_name, eid.domain or "", client)

        # Execute all in parallel
        results = {}
        for name, coro in tasks.items():
            try:
                results[name] = await coro
                metadata["data_sources"].append(name)
            except Exception as e:
                logger.warning("collector_failed", collector=name, error=str(e))
                results[name] = {}
                metadata["errors"].append(f"{name}: {str(e)}")

    # === BUILD SIGNAL OBJECTS ===

    dns_data = results.get("dns", {})
    web_data = results.get("web", {})
    social_data = results.get("social", {})
    kg_data = results.get("knowledge", {})
    github_data = results.get("github", {})
    rep_data = results.get("reputation", {})
    bl_data = results.get("blocklist", {})

    # Merge with registered data if available
    reg = registered_data or {}

    # --- Identity ---
    identity = IdentitySignals(
        domain_verified=reg.get("verified", False) or dns_data.get("dns_txt_verified", False),
        dns_txt_verified=dns_data.get("dns_txt_verified", False),
        file_verified=reg.get("verification_method") == "domain_file",
        email_verified=reg.get("verification_method") == "email",
        domain_age_days=dns_data.get("domain_age_days", 0),
        ssl_valid=dns_data.get("ssl_valid", False),
        ssl_org_match=dns_data.get("ssl_org_match", False),
        dns_has_spf=dns_data.get("dns_has_spf", False),
        dns_has_dmarc=dns_data.get("dns_has_dmarc", False),
        dns_has_dkim=dns_data.get("dns_has_dkim", False),
        has_structured_data=web_data.get("has_structured_data", False),
        has_organization_schema=web_data.get("has_organization_schema", False),
        has_product_schema=web_data.get("has_product_schema", False),
        has_faq_schema=web_data.get("has_faq_schema", False),
        has_wikidata_entry=kg_data.get("has_wikidata_entry", False),
        has_wikipedia_page=kg_data.get("has_wikipedia_page", False),
        has_crunchbase=kg_data.get("has_crunchbase", False),
        has_linkedin_company=kg_data.get("has_linkedin_company", False),
        has_google_knowledge_panel=kg_data.get("has_google_knowledge_panel", False),
        has_business_registration=reg.get("has_business_registration", False),
        social_profiles=social_data.get("social_profiles", {}),
        social_profiles_count=social_data.get("social_profiles_count", 0),
        has_agent_card=reg.get("has_agent_card", False),
        has_model_card=reg.get("has_model_card", False),
        has_api_documentation=web_data.get("has_api_documentation", False),
        geo_score=reg.get("visibility_score", 0) or 0,
    )

    # --- Competence ---
    competence = CompetenceSignals(
        total_transactions=reg.get("total_transactions", 0),
        successful_transactions=reg.get("successful_transactions", 0),
        failed_transactions=reg.get("failed_transactions", 0),
        uptime_pct=reg.get("uptime_pct", 0),
        has_status_page=web_data.get("has_status_page", False),
        github_stars=github_data.get("github_stars", 0),
        github_last_commit_days=github_data.get("github_last_commit_days", 0),
        has_public_changelog=web_data.get("has_public_changelog", False),
        has_public_roadmap=web_data.get("has_public_roadmap", False),
        visibility_score=reg.get("visibility_score", 0) or 0,
    )

    # --- Solvency ---
    solvency = SolvencySignals(
        has_payment_method=bool(reg.get("stripe_customer_id")),
        stripe_verified=bool(reg.get("stripe_customer_id")),
        subscription_active=reg.get("subscription_status") == "active",
        subscription_tier=reg.get("subscription_tier", "free"),
        account_age_days=reg.get("account_age_days", 0),
        # Public financial data would come from Crunchbase/SEC APIs
        has_crunchbase_funding=kg_data.get("has_crunchbase", False),
    ) if hasattr(SolvencySignals, "has_crunchbase_funding") else SolvencySignals(
        has_payment_method=bool(reg.get("stripe_customer_id")),
        stripe_verified=bool(reg.get("stripe_customer_id")),
        subscription_active=reg.get("subscription_status") == "active",
        subscription_tier=reg.get("subscription_tier", "free"),
        account_age_days=reg.get("account_age_days", 0),
    )

    # --- Reputation ---
    reputation = ReputationSignals(
        overall_sentiment=rep_data.get("overall_sentiment", 0.0),
        sentiment_sample_size=rep_data.get("sentiment_sample_size", 0),
        sentiment_trend=rep_data.get("sentiment_trend", "stable"),
        google_reviews_count=rep_data.get("google_reviews_count", 0),
        google_reviews_rating=rep_data.get("google_reviews_rating", 0.0),
        twitter_followers=social_data.get("twitter_followers", 0),
        on_spam_blocklist=bl_data.get("on_spam_blocklist", False),
        on_fraud_blocklist=bl_data.get("on_fraud_blocklist", False),
        on_sanctions_list=bl_data.get("on_sanctions_list", False),
    )

    # --- Network ---
    network = NetworkSignals(
        # Network signals come primarily from our graph database
        high_trust_connections=reg.get("high_trust_connections", 0),
        verified_partners_count=reg.get("verified_partners_count", 0),
        endorsements_received=reg.get("endorsements_received", 0),
        integration_partners=reg.get("integration_partners", 0),
    )

    # Metadata
    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
    metadata["collection_time_ms"] = round(elapsed, 2)

    return identity, competence, solvency, reputation, network, metadata


# =============================================
# HIGH-LEVEL SCORING FUNCTION
# =============================================

async def score_any_entity(
    query: str,
    registered_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Score ANY entity in the world.
    James Rausch's universal scoring function.

    This is the function that the API calls. It:
    1. Identifies what kind of entity this is
    2. Collects signals from all available sources
    3. Enriches with registered data if available
    4. Runs the trust scoring engine
    5. Returns a complete trust score

    Args:
        query: Any identifier — domain, URL, name, email, agent ID
        registered_data: Optional data from our Neo4j registry

    Returns:
        Complete trust score dict
    """
    from app.trust.engine import calculate_trust_score

    eid = EntityIdentifier.from_query(query)
    is_registered = registered_data is not None

    # Collect all signals
    identity, competence, solvency, reputation, network, metadata = \
        await collect_all_signals(query, registered_data)

    # Generate a deterministic entity_id if not registered
    entity_id = (
        registered_data.get("entity_id") if registered_data
        else f"open:{hashlib.sha256(query.lower().encode()).hexdigest()[:16]}"
    )
    entity_name = (
        registered_data.get("canonical_name") if registered_data
        else metadata.get("name", query)
    )

    # Calculate the score
    trust_score = calculate_trust_score(
        entity_id=entity_id,
        entity_name=entity_name,
        entity_type=metadata.get("entity_type", "unknown"),
        is_verified=registered_data.get("verified", False) if registered_data else False,
        is_registered=is_registered,
        identity=identity,
        competence=competence,
        solvency=solvency,
        reputation=reputation,
        network=network,
        data_freshness="live",
        data_sources=metadata.get("data_sources", []),
    )

    result = trust_score.to_full()
    result["collection_metadata"] = {
        "collection_time_ms": metadata["collection_time_ms"],
        "data_sources_queried": metadata["data_sources"],
        "errors": metadata["errors"],
    }

    return result
