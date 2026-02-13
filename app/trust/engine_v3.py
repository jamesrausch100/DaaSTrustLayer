"""
Market2Agent — Trust Scoring Engine v3
Evidence Accumulation Model

DaaS: The data is the product. The score is the interface.

Architecture:
    Layer A — External data sources (Tranco, crt.sh, VirusTotal, DNS, HTTP, WHOIS, Knowledge Graph)
    Layer B — Scoring algorithm (proprietary: evidence accumulation with category caps)
    Layer C — Confidence score (how much data do we actually have?)

Four scoring categories:
    Existence & Age       (max 300)  — "Are you real and established?"
    Security & Integrity  (max 300)  — "Do you take trust seriously?"
    Reputation & Scale    (max 250)  — "Does the world know and trust you?"
    Operational Maturity  (max 150)  — "Do you operate like a real business?"
    ─────────────────────────────────
    Total possible:          1000
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum


# ── Enums ─────────────────────────────────────────

class TrustGrade(str, Enum):
    AAA = "AAA"
    AA  = "AA"
    A   = "A"
    BBB = "BBB"
    BB  = "BB"
    B   = "B"
    CCC = "CCC"
    D   = "D"


class Recommendation(str, Enum):
    PROCEED              = "PROCEED"
    PROCEED_WITH_CAUTION = "PROCEED_WITH_CAUTION"
    MANUAL_REVIEW        = "MANUAL_REVIEW"
    ENHANCED_DUE_DILIGENCE = "ENHANCED_DUE_DILIGENCE"
    REJECT               = "REJECT"


# ── Raw Signal Input ──────────────────────────────

@dataclass
class RawSignals:
    """
    Every fact we know about an entity. Populated by Layer A collectors.
    No scoring logic here — just data.
    """
    target: str = ""

    # WHOIS
    domain_age_days: int = 0
    whois_org: str = ""
    whois_registrar: str = ""
    domain_expiry_years_ahead: float = 0.0

    # crt.sh (Certificate Transparency)
    ssl_valid: bool = False
    ssl_cert_type: str = ""                 # "EV", "OV", "DV", ""
    ssl_org: str = ""
    first_cert_days_ago: int = 0
    total_certs_issued: int = 0
    cert_issuer: str = ""

    # VirusTotal
    vt_malicious_count: int = 0             # out of ~70 vendors
    vt_suspicious_count: int = 0
    vt_clean_count: int = 0
    vt_community_score: int = 0             # positive = good
    vt_categories: List[str] = field(default_factory=list)
    vt_queried: bool = False                # did we actually reach VT?

    # Google Safe Browsing
    gsb_flagged: bool = False
    gsb_queried: bool = False

    # DNS
    dns_has_spf: bool = False
    dns_has_dmarc: bool = False
    dns_has_dkim: bool = False
    dns_has_dnssec: bool = False
    dns_has_mx: bool = False

    # HTTP Security Headers
    http_has_hsts: bool = False
    http_has_csp: bool = False
    http_has_xframe: bool = False
    http_has_xcontent_type: bool = False
    http_has_referrer_policy: bool = False
    http_has_permissions_policy: bool = False
    http_status: int = 0

    # Knowledge Graph
    has_wikipedia: bool = False
    has_wikidata: bool = False
    has_crunchbase: bool = False
    wikidata_employee_count: int = 0
    wikidata_founding_year: int = 0
    wikidata_industry: str = ""

    # Social Presence
    social_twitter: bool = False
    social_linkedin: bool = False
    social_github: bool = False
    social_facebook: bool = False
    social_youtube: bool = False
    social_instagram: bool = False
    social_count: int = 0

    # Tranco Ranking
    tranco_rank: int = 0                    # 0 = not in top 1M

    # Web Presence
    has_structured_data: bool = False
    has_org_schema: bool = False
    has_status_page: bool = False
    has_api_docs: bool = False
    has_changelog: bool = False
    has_security_txt: bool = False
    has_robots_txt: bool = False

    # Blocklists (beyond VT)
    on_spam_blocklist: bool = False
    on_fraud_blocklist: bool = False
    on_sanctions_list: bool = False

    # Metadata
    sources_queried: List[str] = field(default_factory=list)
    sources_responded: List[str] = field(default_factory=list)
    collection_errors: List[str] = field(default_factory=list)
    collection_time_ms: float = 0.0


# ── Scoring Engine ────────────────────────────────

_EA_CAP = 300   # Existence & Age
_SI_CAP = 300   # Security & Integrity
_RS_CAP = 250   # Reputation & Scale
_OM_CAP = 150   # Operational Maturity


def score_existence_and_age(s: RawSignals) -> tuple[float, Dict[str, float]]:
    """Category: Are you real and established?"""
    breakdown = {}

    # Domain age (WHOIS)
    if s.domain_age_days > 3650:
        breakdown["domain_age"] = 80
    elif s.domain_age_days > 1825:
        breakdown["domain_age"] = 60
    elif s.domain_age_days > 730:
        breakdown["domain_age"] = 40
    elif s.domain_age_days > 365:
        breakdown["domain_age"] = 25
    elif s.domain_age_days > 90:
        breakdown["domain_age"] = 10
    elif s.domain_age_days > 0 and s.domain_age_days < 30:
        breakdown["domain_age_penalty"] = -50

    # SSL cert exists
    if s.ssl_valid:
        breakdown["ssl_exists"] = 15

    # Certificate Transparency — first cert age (immutable proof)
    if s.first_cert_days_ago > 1825:
        breakdown["first_cert_age"] = 30
    elif s.first_cert_days_ago > 730:
        breakdown["first_cert_age"] = 20
    elif s.first_cert_days_ago > 365:
        breakdown["first_cert_age"] = 10

    # Cert type (CA verified identity)
    if s.ssl_cert_type == "EV":
        breakdown["cert_type"] = 25
    elif s.ssl_cert_type == "OV":
        breakdown["cert_type"] = 10

    # Knowledge graph — Wikipedia is hard to fake
    if s.has_wikipedia:
        breakdown["wikipedia"] = 50
    if s.has_wikidata:
        breakdown["wikidata"] = 30
    if s.has_crunchbase:
        breakdown["crunchbase"] = 20

    # Cross-source consistency
    if s.whois_org and s.ssl_org and s.whois_org.lower()[:8] == s.ssl_org.lower()[:8]:
        breakdown["org_consistency"] = 20

    # Long-term domain commitment
    if s.domain_expiry_years_ahead >= 3:
        breakdown["domain_commitment"] = 15
    elif s.domain_expiry_years_ahead >= 1:
        breakdown["domain_commitment"] = 5

    raw = sum(breakdown.values())
    return min(max(raw, 0), _EA_CAP), breakdown


def score_security_and_integrity(s: RawSignals) -> tuple[float, Dict[str, float]]:
    """Category: Do you take trust seriously?"""
    breakdown = {}

    # VirusTotal — the 70-vendor consensus
    if s.vt_queried:
        flagged = s.vt_malicious_count + s.vt_suspicious_count
        if flagged == 0:
            breakdown["vt_clean"] = 100
        elif flagged <= 2:
            breakdown["vt_mostly_clean"] = 40
        elif flagged <= 5:
            breakdown["vt_suspicious_penalty"] = -50
        else:
            breakdown["vt_dangerous_penalty"] = -200

    # Google Safe Browsing
    if s.gsb_queried:
        if not s.gsb_flagged:
            breakdown["gsb_clean"] = 30
        else:
            breakdown["gsb_flagged_penalty"] = -200

    # DNS security
    if s.dns_has_spf:
        breakdown["spf"] = 25
    if s.dns_has_dmarc:
        breakdown["dmarc"] = 30
    if s.dns_has_dkim:
        breakdown["dkim"] = 15
    if s.dns_has_dnssec:
        breakdown["dnssec"] = 20

    # HTTP headers
    if s.http_has_hsts:
        breakdown["hsts"] = 20
    if s.http_has_csp:
        breakdown["csp"] = 20
    if s.http_has_xframe:
        breakdown["xframe"] = 10
    if s.http_has_xcontent_type:
        breakdown["xcontent"] = 10
    if s.http_has_referrer_policy:
        breakdown["referrer_policy"] = 10
    if s.http_has_permissions_policy:
        breakdown["permissions_policy"] = 10

    raw = sum(breakdown.values())
    return min(max(raw, 0), _SI_CAP), breakdown


def score_reputation_and_scale(s: RawSignals) -> tuple[float, Dict[str, float]]:
    """Category: Does the world know and trust you?"""
    breakdown = {}

    # Tranco rank — the internet's popularity contest
    if s.tranco_rank > 0:
        if s.tranco_rank <= 100:
            breakdown["tranco"] = 100
        elif s.tranco_rank <= 1000:
            breakdown["tranco"] = 80
        elif s.tranco_rank <= 10_000:
            breakdown["tranco"] = 60
        elif s.tranco_rank <= 100_000:
            breakdown["tranco"] = 40
        elif s.tranco_rank <= 500_000:
            breakdown["tranco"] = 20
        else:
            breakdown["tranco"] = 10

    # VirusTotal community score
    if s.vt_community_score > 0:
        breakdown["vt_community"] = min(s.vt_community_score * 3, 30)

    # Social presence
    if s.social_twitter:
        breakdown["twitter"] = 10
    if s.social_linkedin:
        breakdown["linkedin"] = 15
    if s.social_github:
        breakdown["github"] = 15
    if s.social_count >= 4:
        breakdown["social_breadth"] = 20

    raw = sum(breakdown.values())
    return min(max(raw, 0), _RS_CAP), breakdown


def score_operational_maturity(s: RawSignals) -> tuple[float, Dict[str, float]]:
    """Category: Do you operate like a real business?"""
    breakdown = {}

    if s.has_structured_data:
        breakdown["structured_data"] = 15
    if s.has_org_schema:
        breakdown["org_schema"] = 15
    if s.has_status_page:
        breakdown["status_page"] = 25
    if s.has_api_docs:
        breakdown["api_docs"] = 20
    if s.has_changelog:
        breakdown["changelog"] = 15
    if s.has_security_txt:
        breakdown["security_txt"] = 15
    if s.total_certs_issued > 5:
        breakdown["cert_renewals"] = 15
    if s.dns_has_mx:
        breakdown["mx_records"] = 15
    if s.has_robots_txt:
        breakdown["robots_txt"] = 15

    raw = sum(breakdown.values())
    return min(max(raw, 0), _OM_CAP), breakdown


# ── Hard Caps (penalties override everything) ─────

def apply_hard_caps(score: int, s: RawSignals) -> tuple[int, Optional[str]]:
    """
    Certain signals override the total score. Trust is built slowly
    and destroyed instantly.
    """
    if s.on_sanctions_list:
        return 0, "SANCTIONED"

    if s.on_fraud_blocklist:
        return min(score, 50), "FRAUD_BLOCKLISTED"

    if s.gsb_queried and s.gsb_flagged:
        return min(score, 150), "GOOGLE_SAFE_BROWSING_FLAGGED"

    if s.vt_queried and (s.vt_malicious_count + s.vt_suspicious_count) >= 6:
        return min(score, 200), "VIRUSTOTAL_HIGH_FLAGS"

    if s.domain_age_days > 0 and s.domain_age_days < 30 and s.tranco_rank == 0:
        return min(score, 100), "NEW_DOMAIN_NO_REPUTATION"

    return score, None


# ── Main Entry Point ──────────────────────────────

@dataclass
class TrustScoreResult:
    """The final output. Every field is API-ready."""
    target: str
    score: int
    grade: TrustGrade
    recommendation: Recommendation

    # Category breakdowns
    existence_age: float
    security_integrity: float
    reputation_scale: float
    operational_maturity: float

    # Full signal breakdown (for paid tier)
    breakdown: Dict[str, Dict[str, float]] = field(default_factory=dict)

    # Confidence
    confidence: float = 0.0
    confidence_label: str = "unknown"
    sources_used: int = 0
    sources_total: int = 0

    # Caps
    cap_applied: Optional[str] = None

    # Metadata
    engine_version: str = "3.0.0"

    def to_preview(self) -> Dict[str, Any]:
        """Free tier — score, grade, recommendation. No breakdown."""
        return {
            "target": self.target,
            "score": self.score,
            "grade": self.grade.value,
            "recommendation": self.recommendation.value,
            "confidence": round(self.confidence, 2),
            "confidence_label": self.confidence_label,
            "is_verified": self.score >= 650,
            "is_registered": False,
            "engine_version": self.engine_version,
            "message": "Full breakdown available with an API key. Get yours at market2agent.ai",
        }

    def to_full(self) -> Dict[str, Any]:
        """Paid tier — everything."""
        return {
            "target": self.target,
            "score": self.score,
            "grade": self.grade.value,
            "recommendation": self.recommendation.value,
            "confidence": round(self.confidence, 2),
            "confidence_label": self.confidence_label,
            "existence_age_score": self.existence_age,
            "security_integrity_score": self.security_integrity,
            "reputation_scale_score": self.reputation_scale,
            "operational_maturity_score": self.operational_maturity,
            "category_caps": {
                "existence_age": _EA_CAP,
                "security_integrity": _SI_CAP,
                "reputation_scale": _RS_CAP,
                "operational_maturity": _OM_CAP,
            },
            "breakdown": self.breakdown,
            "sources_used": self.sources_used,
            "sources_total": self.sources_total,
            "cap_applied": self.cap_applied,
            "is_verified": self.score >= 650,
            "is_registered": False,
            "engine_version": self.engine_version,
        }


def compute_score(signals: RawSignals) -> TrustScoreResult:
    """
    THE scoring function. Layer B. The proprietary algorithm.
    Takes raw signals, returns a trust score with full breakdown.
    """
    # Score each category
    ea, ea_bd = score_existence_and_age(signals)
    si, si_bd = score_security_and_integrity(signals)
    rs, rs_bd = score_reputation_and_scale(signals)
    om, om_bd = score_operational_maturity(signals)

    raw_score = int(ea + si + rs + om)

    # Apply hard caps
    final_score, cap = apply_hard_caps(raw_score, signals)

    # Grade
    if final_score >= 850:
        grade = TrustGrade.AAA
    elif final_score >= 750:
        grade = TrustGrade.AA
    elif final_score >= 650:
        grade = TrustGrade.A
    elif final_score >= 550:
        grade = TrustGrade.BBB
    elif final_score >= 450:
        grade = TrustGrade.BB
    elif final_score >= 350:
        grade = TrustGrade.B
    elif final_score >= 200:
        grade = TrustGrade.CCC
    else:
        grade = TrustGrade.D

    # Recommendation
    if final_score >= 650:
        rec = Recommendation.PROCEED
    elif final_score >= 450:
        rec = Recommendation.PROCEED_WITH_CAUTION
    elif final_score >= 350:
        rec = Recommendation.MANUAL_REVIEW
    elif final_score >= 200:
        rec = Recommendation.ENHANCED_DUE_DILIGENCE
    else:
        rec = Recommendation.REJECT

    # Confidence
    total_sources = 9  # tranco, crt.sh, vt, dns, http, whois, kg, web
    responded = len(signals.sources_responded)
    confidence = responded / total_sources if total_sources > 0 else 0.0

    if confidence >= 0.8:
        conf_label = "high"
    elif confidence >= 0.5:
        conf_label = "moderate"
    else:
        conf_label = "low"

    return TrustScoreResult(
        target=signals.target,
        score=final_score,
        grade=grade,
        recommendation=rec,
        existence_age=ea,
        security_integrity=si,
        reputation_scale=rs,
        operational_maturity=om,
        breakdown={
            "existence_age": ea_bd,
            "security_integrity": si_bd,
            "reputation_scale": rs_bd,
            "operational_maturity": om_bd,
        },
        confidence=confidence,
        confidence_label=conf_label,
        sources_used=responded,
        sources_total=total_sources,
        cap_applied=cap,
    )
