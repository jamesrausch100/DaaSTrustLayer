"""
Market2Agent — Sensor Network (Collectors v4)
Every collector is a sensor. Every result is a block on the chain.

The collectors themselves are unchanged from v3.
This module wraps them to record every observation to the TrustChain.
"""
import asyncio
import time
from typing import Dict, Any

import httpx
import structlog

from app.compute.collectors_v3 import (
    parse_target,
    collect_tranco,
    collect_crtsh,
    collect_virustotal,
    collect_dns,
    collect_http_headers,
    collect_whois,
    collect_knowledge_graph,
    collect_web_presence,
    collect_social,
)
from app.chain.trustchain import record_observation, Sensor
from app.trust.engine_v3 import RawSignals

logger = structlog.get_logger()

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


async def observe_entity(target: str) -> RawSignals:
    """
    Run all sensors on an entity. Record every observation to the chain.
    Returns a RawSignals object for scoring.

    This is the difference between a scraper and a sensor network:
    - Scraper: fetch data, compute score, throw away data
    - Sensor: fetch data, RECORD data, THEN compute score

    The recorded data is the product. The score is the interface.
    """
    start = time.time()
    parsed = parse_target(target)
    domain = parsed["domain"]
    name = parsed["name"]
    entity_id = domain or target.lower().strip()

    raw = RawSignals(target=target)
    raw.sources_queried = []

    async with httpx.AsyncClient(
        headers={"User-Agent": "Market2Agent Sensor/4.0 (+https://market2agent.ai/bot)"},
        follow_redirects=True,
        verify=True,
        timeout=_TIMEOUT,
    ) as client:

        # Build sensor tasks
        sensors = {}
        if domain:
            sensors[Sensor.TRANCO] = collect_tranco(domain)
            sensors[Sensor.CRTSH] = collect_crtsh(domain, client)
            sensors[Sensor.VIRUSTOTAL] = collect_virustotal(domain, client)
            sensors[Sensor.DNS] = collect_dns(domain)
            sensors[Sensor.HTTP_HEADERS] = collect_http_headers(domain, client)
            sensors[Sensor.WHOIS] = collect_whois(domain)
            sensors[Sensor.WEB_PRESENCE] = collect_web_presence(domain, client)

        sensors[Sensor.KNOWLEDGE_GRAPH] = collect_knowledge_graph(name, domain, client)
        sensors[Sensor.SOCIAL] = collect_social(name, domain, client)

        raw.sources_queried = [s.value for s in sensors.keys()]

        # Fire all sensors in parallel
        keys = list(sensors.keys())
        coros = [_timed_collect(s.value, c) for s, c in sensors.items()]
        results_list = await asyncio.gather(*coros, return_exceptions=True)

        # Process results: record to chain + build RawSignals
        results = {}
        for i, res in enumerate(results_list):
            sensor = keys[i]
            if isinstance(res, tuple):
                sensor_name, signals, elapsed_ms = res
                results[sensor] = signals
                raw.sources_responded.append(sensor.value)

                # === RECORD TO CHAIN ===
                try:
                    record_observation(
                        entity_id=entity_id,
                        sensor=sensor.value,
                        signals=signals,
                        collection_time_ms=elapsed_ms,
                    )
                except Exception as e:
                    logger.warning("chain_record_failed", sensor=sensor.value, error=str(e))

            elif isinstance(res, Exception):
                raw.collection_errors.append(f"{sensor.value}: {str(res)[:100]}")
                results[sensor] = {}

    # Map sensor outputs → RawSignals
    tr = results.get(Sensor.TRANCO, {})
    ct = results.get(Sensor.CRTSH, {})
    vt = results.get(Sensor.VIRUSTOTAL, {})
    dn = results.get(Sensor.DNS, {})
    ht = results.get(Sensor.HTTP_HEADERS, {})
    wh = results.get(Sensor.WHOIS, {})
    kg = results.get(Sensor.KNOWLEDGE_GRAPH, {})
    wp = results.get(Sensor.WEB_PRESENCE, {})
    so = results.get(Sensor.SOCIAL, {})

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

    # Record the score itself as a block too
    from app.trust.engine_v3 import compute_score
    score_result = compute_score(raw)

    try:
        record_observation(
            entity_id=entity_id,
            sensor=Sensor.SCORE.value,
            signals={
                "score": score_result.score,
                "grade": score_result.grade.value,
                "recommendation": score_result.recommendation.value,
                "confidence": score_result.confidence,
                "ea": score_result.existence_age,
                "si": score_result.security_integrity,
                "rs": score_result.reputation_scale,
                "om": score_result.operational_maturity,
                "cap": score_result.cap_applied,
            },
        )
    except Exception as e:
        logger.warning("score_chain_record_failed", error=str(e))

    return raw


async def _timed_collect(name: str, coro):
    """Run a sensor with timing. Returns (name, signals, elapsed_ms)."""
    t0 = time.time()
    try:
        result = await asyncio.wait_for(coro, timeout=15.0)
        elapsed = round((time.time() - t0) * 1000, 2)
        return name, result, elapsed
    except Exception as e:
        elapsed = round((time.time() - t0) * 1000, 2)
        logger.debug("sensor_failed", name=name, error=str(e), elapsed_ms=elapsed)
        return name, {}, elapsed
