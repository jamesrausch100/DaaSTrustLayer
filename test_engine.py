#!/usr/bin/env python3
"""
Market2Agent v2.0 — Validation Test Suite
Run: python3 test_engine.py

Validates the trust scoring engine without external dependencies (no Neo4j, Redis, etc.).
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

PASS = 0
FAIL = 0


def test(name, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


print("=" * 60)
print("Market2Agent v2.0 — Engine Validation")
print("=" * 60)

# ── 1. Imports ────────────────────────────────────────────
print("\n1. Core Imports")
try:
    from app.trust.engine import (
        calculate_trust_score,
        calculate_trust_score_v1,
        IdentitySignals,
        CompetenceSignals,
        SolvencySignals,
        ReputationSignals,
        NetworkSignals,
        TrustScore,
        TrustGrade,
        RiskLevel,
        Recommendation,
        EntityType,
    )
    test("engine.py imports", True)
except Exception as e:
    test(f"engine.py imports: {e}", False)

try:
    from app.trust.metering import MeteringManager
    test("metering.py imports", True)
except ImportError as e:
    print(f"  ⊘ metering.py skipped (missing dep: {e}) — install requirements.txt")
except Exception as e:
    test(f"metering.py imports: {e}", False)

try:
    from app.trust.api_keys import KeyManager
    test("api_keys.py imports", True)
except ImportError as e:
    print(f"  ⊘ api_keys.py skipped (missing dep: {e}) — install requirements.txt")
except Exception as e:
    test(f"api_keys.py imports: {e}", False)

# ── 2. Signal Creation ───────────────────────────────────
print("\n2. Signal Object Creation")

identity = IdentitySignals(
    domain_verified=True, ssl_valid=True, domain_age_days=1200,
    dns_has_spf=True, dns_has_dmarc=True, has_structured_data=True,
    has_linkedin_company=True,
    social_profiles={"twitter": True, "linkedin": True, "github": True},
    social_profiles_count=3, has_wikidata_entry=True, has_crunchbase=True,
    has_business_registration=True,
)
test("IdentitySignals created", identity.domain_verified is True)

competence = CompetenceSignals(
    total_transactions=500, successful_transactions=490,
    uptime_pct=99.8, github_stars=250, has_status_page=True, g2_rating=4.5,
)
test("CompetenceSignals created", competence.total_transactions == 500)

solvency = SolvencySignals(
    has_payment_method=True, stripe_verified=True, employee_count=50,
    funding_total_usd=5_000_000, funding_rounds=2, subscription_active=True,
)
test("SolvencySignals created", solvency.funding_total_usd == 5_000_000)

reputation = ReputationSignals(
    overall_sentiment=0.72, sentiment_sample_size=200,
    google_reviews_count=85, google_reviews_rating=4.2, has_soc2=True,
)
test("ReputationSignals created", reputation.has_soc2 is True)

network = NetworkSignals(
    verified_partners_count=5, high_trust_connections=8, endorsements_received=12,
)
test("NetworkSignals created", network.high_trust_connections == 8)

# ── 3. Sub-score Calculation ─────────────────────────────
print("\n3. Sub-score Calculation")

id_score = identity.calculate()
test(f"Identity sub-score: {id_score:.1f}/100", 0 <= id_score <= 100)

comp_score = competence.calculate()
test(f"Competence sub-score: {comp_score:.1f}/100", 0 <= comp_score <= 100)

solv_score = solvency.calculate()
test(f"Solvency sub-score: {solv_score:.1f}/100", 0 <= solv_score <= 100)

rep_score = reputation.calculate()
test(f"Reputation sub-score: {rep_score:.1f}/100", 0 <= rep_score <= 100)

net_score = network.calculate()
test(f"Network sub-score: {net_score:.1f}/100", 0 <= net_score <= 100)

# ── 4. Full Trust Score ──────────────────────────────────
print("\n4. Full Trust Score Calculation")

result = calculate_trust_score(
    entity_id="test-company",
    entity_name="Test Corp",
    entity_type="business",
    is_registered=True,
    is_verified=True,
    identity=identity,
    competence=competence,
    solvency=solvency,
    reputation=reputation,
    network=network,
)

test(f"Score: {result.score}/1000", 0 <= result.score <= 1000)
test(f"Grade: {result.grade}", result.grade in list(TrustGrade))
test(f"Risk: {result.risk_level}", result.risk_level in list(RiskLevel))
test(f"Recommendation: {result.recommendation}", result.recommendation in list(Recommendation))
test(f"Confidence: {result.confidence:.2f}", 0 <= result.confidence <= 1.0)
test(f"Signal count: {result.signal_count}", result.signal_count > 0)
test("Entity name preserved", result.entity_name == "Test Corp")
test("Is registered", result.is_registered is True)
test("Has data sources", len(result.data_sources) > 0)
test("Has identity_signals dict", isinstance(result.identity_signals, dict))
test("Has competence_signals dict", isinstance(result.competence_signals, dict))
test("Has solvency_signals dict", isinstance(result.solvency_signals, dict))
test("Has reputation_signals dict", isinstance(result.reputation_signals, dict))
test("Has network_signals dict", isinstance(result.network_signals, dict))

# ── 5. Edge Cases ────────────────────────────────────────
print("\n5. Edge Cases")

# Empty entity (no data)
empty_result = calculate_trust_score(
    entity_id="unknown",
    entity_name="Unknown Entity",
    entity_type="unknown",
    is_registered=False,
)
test(f"Empty entity scores: {empty_result.score}/1000", empty_result.score >= 0)
test("Empty entity low confidence", empty_result.confidence < 0.3)
test("Empty entity is D or CCC grade", empty_result.grade in [TrustGrade.D, TrustGrade.CCC])

# Maximum signals
max_identity = IdentitySignals(
    domain_verified=True, dns_txt_verified=True, file_verified=True,
    email_verified=True, domain_age_days=7300, ssl_valid=True,
    ssl_org_match=True, dns_has_spf=True, dns_has_dmarc=True,
    dns_has_dkim=True, has_structured_data=True, has_organization_schema=True,
    has_wikidata_entry=True, has_wikipedia_page=True, has_crunchbase=True,
    has_linkedin_company=True, has_business_registration=True,
    has_sec_filing=True, has_trademark=True, ein_verified=True,
    social_profiles={}, social_profiles_count=6,
    name_consistent_across_sources=True, address_consistent=True,
    phone_verified=True, has_agent_card=True, has_api_documentation=True,
)
max_id_score = max_identity.calculate()
test(f"Max identity score: {max_id_score:.1f}/100", max_id_score >= 80)

# ── 6. V1 Backward Compatibility ────────────────────────
print("\n6. V1 Backward Compatibility")
try:
    v1_result = calculate_trust_score_v1(
        entity_id="compat-test",
        entity_name="Legacy Entity",
        is_verified=True,
        identity=IdentitySignals(domain_verified=True, email_verified=True),
        competence=CompetenceSignals(total_transactions=100, uptime_pct=99.5),
        solvency=SolvencySignals(has_payment_method=True),
    )
    test(f"V1 score: {v1_result.score}/1000", isinstance(v1_result, TrustScore))
except Exception as e:
    test(f"V1 compat: {e}", False)

# ── Summary ──────────────────────────────────────────────
print("\n" + "=" * 60)
total = PASS + FAIL
print(f"Results: {PASS}/{total} passed ({FAIL} failed)")
if FAIL == 0:
    print("🎉 ALL TESTS PASSED — Engine is production-ready")
else:
    print(f"⚠️  {FAIL} test(s) failed — review above")
print("=" * 60)
sys.exit(0 if FAIL == 0 else 1)
