"""
Market2Agent — Universal Trust Scoring Engine
Conceived and architected by James Rausch, Lead Visionary & Pilot of the Trust Score Revolution.

The FICO for the AI Economy — but for EVERYTHING. Not just registered entities.
This engine scores any entity on Earth: businesses, AI agents, APIs, individuals,
DAOs, smart contracts, domains — registered or not.

Architecture:
    Registered entities  → Full score from Neo4j + public signals (highest confidence)
    Known entities        → Score from public web signals (medium confidence)
    Unknown entities      → Score from whatever we can find (low confidence, still useful)

Trust Score = f(Identity, Competence, Solvency, Reputation, Network)

    Identity Risk    (0-100, weight 30%): Is the entity real and verified?
    Competence Risk  (0-100, weight 25%): Does it perform reliably?
    Solvency Risk    (0-100, weight 15%): Can it honor commitments?
    Reputation Risk  (0-100, weight 20%): What does the world say about it?
    Network Risk     (0-100, weight 10%): Who vouches for it? Who does it associate with?

Score Ranges:
    900-1000  AAA  — Institutional grade (verified enterprise + deep history + stellar reputation)
    800-899   AA   — High trust (verified + good track record + positive sentiment)
    700-799   A    — Standard trust (verified, some history, neutral-positive reputation)
    600-699   BBB  — Acceptable (partially verified, limited but clean history)
    500-599   BB   — Caution (minimal verification, thin history, mixed signals)
    400-499   B    — Elevated risk (unverified but some public presence)
    200-399   CCC  — High risk (unverified, minimal data, negative signals)
    0-199     D    — Critical risk (no data, flagged, or adversarial signals)

James Rausch's vision: "Every entity that participates in the digital economy
deserves a trust score — and every entity that transacts deserves to check one."
"""
import math
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Tuple
from enum import Enum


# =============================================
# ENUMS
# =============================================

class TrustGrade(str, Enum):
    AAA = "AAA"
    AA  = "AA"
    A   = "A"
    BBB = "BBB"
    BB  = "BB"
    B   = "B"
    CCC = "CCC"
    D   = "D"


class RiskLevel(str, Enum):
    MINIMAL  = "minimal"
    LOW      = "low"
    MODERATE = "moderate"
    ELEVATED = "elevated"
    HIGH     = "high"
    SEVERE   = "severe"
    CRITICAL = "critical"


class Recommendation(str, Enum):
    PROCEED              = "PROCEED"
    PROCEED_WITH_CAUTION = "PROCEED_WITH_CAUTION"
    MANUAL_REVIEW        = "MANUAL_REVIEW"
    ENHANCED_DUE_DILIGENCE = "ENHANCED_DUE_DILIGENCE"
    REJECT               = "REJECT"


class EntityType(str, Enum):
    """What kind of entity is being scored."""
    BUSINESS     = "business"
    AI_AGENT     = "ai_agent"
    API_SERVICE  = "api_service"
    INDIVIDUAL   = "individual"
    DAO          = "dao"
    SMART_CONTRACT = "smart_contract"
    DOMAIN       = "domain"
    UNKNOWN      = "unknown"


class DataSource(str, Enum):
    """Where the scoring data came from."""
    REGISTRY     = "registry"       # Our Neo4j database (highest trust)
    PUBLIC_WEB   = "public_web"     # Open web signals (DNS, WHOIS, structured data)
    SOCIAL_GRAPH = "social_graph"   # Social media presence and sentiment
    BLOCKCHAIN   = "blockchain"     # On-chain data for Web3 entities
    GOVERNMENT   = "government"     # Business registrations, SEC filings
    COMMUNITY    = "community"      # User reports, feedback, reviews
    INFERRED     = "inferred"       # ML-inferred from available data


# =============================================
# SIGNAL DATACLASSES — Each pillar of trust
# =============================================

@dataclass
class IdentitySignals:
    """
    Signals used to assess Identity Risk.
    Answers: "Is this entity real, and is it who it claims to be?"

    Works for ANY entity — registered or not.
    Unregistered entities are scored purely from public signals.
    """
    # Domain & web verification
    domain_verified: bool = False
    dns_txt_verified: bool = False
    file_verified: bool = False
    email_verified: bool = False
    domain_age_days: int = 0
    ssl_valid: bool = False
    ssl_org_match: bool = False            # Does SSL cert org match claimed identity?
    dns_has_spf: bool = False
    dns_has_dmarc: bool = False
    dns_has_dkim: bool = False

    # Structured data & SEO presence
    has_structured_data: bool = False
    has_organization_schema: bool = False
    has_product_schema: bool = False
    has_faq_schema: bool = False

    # Knowledge graph presence
    has_wikidata_entry: bool = False
    has_wikipedia_page: bool = False
    has_crunchbase: bool = False
    has_linkedin_company: bool = False
    has_google_knowledge_panel: bool = False

    # Government / official records
    has_business_registration: bool = False
    has_sec_filing: bool = False
    has_trademark: bool = False
    incorporation_state: Optional[str] = None
    ein_verified: bool = False

    # Social presence (count of verified profiles)
    social_profiles: Dict[str, bool] = field(default_factory=dict)
    social_profiles_count: int = 0

    # Consistency checks
    name_consistent_across_sources: bool = False
    address_consistent: bool = False
    phone_verified: bool = False

    # AI Agent specific
    has_agent_card: bool = False            # A2A agent card published
    has_model_card: bool = False            # ML model card available
    has_api_documentation: bool = False

    # Derived
    geo_score: float = 0.0

    def calculate(self) -> float:
        """Calculate identity sub-score (0-100). Works for ANY entity."""
        score = 0.0

        # === TIER 1: Hard verification (max 30 pts) ===
        if self.domain_verified or self.dns_txt_verified or self.file_verified:
            score += 18
        if self.email_verified:
            score += 7
        if self.ssl_org_match:
            score += 5

        # === TIER 2: Structured web presence (max 15 pts) ===
        if self.has_structured_data:
            score += 5
        if self.has_organization_schema:
            score += 5
        if self.has_product_schema or self.has_faq_schema:
            score += 3
        if self.ssl_valid:
            score += 2

        # === TIER 3: Knowledge graph / authority (max 20 pts) ===
        if self.has_wikidata_entry:
            score += 7
        if self.has_wikipedia_page:
            score += 7
        if self.has_google_knowledge_panel:
            score += 6

        # === TIER 4: Official records (max 15 pts) ===
        if self.has_business_registration:
            score += 5
        if self.has_sec_filing:
            score += 4
        if self.has_trademark:
            score += 3
        if self.ein_verified:
            score += 3

        # === TIER 5: Web presence breadth (max 12 pts) ===
        social_score = min(self.social_profiles_count * 2, 6)
        score += social_score

        if self.has_crunchbase:
            score += 2
        if self.has_linkedin_company:
            score += 2
        if self.has_agent_card or self.has_model_card:
            score += 2

        # === TIER 6: Domain maturity (max 8 pts) ===
        if self.domain_age_days > 3650:      # 10+ years
            score += 8
        elif self.domain_age_days > 1825:    # 5+ years
            score += 6
        elif self.domain_age_days > 365:     # 1+ year
            score += 4
        elif self.domain_age_days > 90:
            score += 2
        elif self.domain_age_days > 30:
            score += 1

        # === Consistency bonus (max 5 pts) ===
        if self.name_consistent_across_sources:
            score += 3
        if self.address_consistent:
            score += 2

        # === Email security signals (max 3 pts) ===
        if self.dns_has_spf:
            score += 1
        if self.dns_has_dmarc:
            score += 1
        if self.dns_has_dkim:
            score += 1

        return min(score, 100.0)


@dataclass
class CompetenceSignals:
    """
    Signals used to assess Competence Risk.
    Answers: "Does this entity deliver on its promises?"

    For registered entities: transaction data from our platform.
    For unregistered entities: public performance signals.
    """
    # Transaction history (internal — from our platform)
    total_transactions: int = 0
    successful_transactions: int = 0
    failed_transactions: int = 0
    disputed_transactions: int = 0

    # Performance metrics
    avg_response_time_ms: float = 0.0
    uptime_pct: float = 0.0
    p99_latency_ms: float = 0.0
    last_active: Optional[datetime] = None
    error_rate_7d: float = 0.0
    error_rate_30d: float = 0.0

    # AI-specific quality metrics
    hallucination_reports: int = 0
    accuracy_score: float = 0.0
    safety_score: float = 0.0
    bias_reports: int = 0

    # Public competence signals (for unregistered entities)
    has_status_page: bool = False
    status_page_uptime: float = 0.0        # From public status pages
    github_stars: int = 0
    github_last_commit_days: int = 0
    npm_weekly_downloads: int = 0
    pypi_weekly_downloads: int = 0
    has_public_changelog: bool = False
    has_public_roadmap: bool = False
    stack_overflow_score: int = 0
    g2_rating: float = 0.0
    trustpilot_rating: float = 0.0
    app_store_rating: float = 0.0

    # API-specific signals
    has_public_api: bool = False
    api_response_time_ms: float = 0.0
    has_rate_limiting: bool = False
    has_versioning: bool = False
    has_deprecation_policy: bool = False

    # Visibility index
    visibility_score: float = 0.0

    def calculate(self) -> float:
        """Calculate competence sub-score (0-100)."""
        score = 0.0

        # === Internal transaction data (max 35 pts) ===
        if self.total_transactions > 0:
            success_rate = self.successful_transactions / self.total_transactions
            score += success_rate * 20

            # Volume bonus
            if self.total_transactions > 10000:
                score += 10
            elif self.total_transactions > 1000:
                score += 8
            elif self.total_transactions > 100:
                score += 5
            elif self.total_transactions > 10:
                score += 3
            elif self.total_transactions > 0:
                score += 1

            # Dispute penalty
            if self.total_transactions > 10:
                dispute_rate = self.disputed_transactions / self.total_transactions
                if dispute_rate > 0.05:
                    score -= 10
                elif dispute_rate > 0.02:
                    score -= 5
                elif dispute_rate > 0:
                    score -= 2

        # === Reliability metrics (max 15 pts) ===
        if self.uptime_pct > 99.9:
            score += 10
        elif self.uptime_pct > 99:
            score += 7
        elif self.uptime_pct > 95:
            score += 4
        elif self.uptime_pct > 0:
            score += 1

        if self.has_status_page:
            score += 3
            if self.status_page_uptime > 99:
                score += 2

        # === Response time (max 8 pts) ===
        effective_rt = self.avg_response_time_ms or self.api_response_time_ms
        if effective_rt > 0:
            if effective_rt < 100:
                score += 8
            elif effective_rt < 200:
                score += 6
            elif effective_rt < 500:
                score += 4
            elif effective_rt < 1000:
                score += 2
            else:
                score += 1

        # === AI quality (max 12 pts) ===
        if self.hallucination_reports == 0 and self.total_transactions > 10:
            score += 6
        elif self.hallucination_reports < 3:
            score += 3

        if self.accuracy_score > 0:
            score += (self.accuracy_score / 100) * 4

        if self.safety_score > 80:
            score += 2

        # === Public reputation signals (max 15 pts) ===
        # These allow scoring of entities we've never seen before
        public_rep_score = 0.0
        if self.g2_rating > 0:
            public_rep_score += min(self.g2_rating, 5) * 1.0
        if self.trustpilot_rating > 0:
            public_rep_score += min(self.trustpilot_rating, 5) * 0.6
        if self.app_store_rating > 0:
            public_rep_score += min(self.app_store_rating, 5) * 0.4
        score += min(public_rep_score, 8)

        if self.github_stars > 1000:
            score += 4
        elif self.github_stars > 100:
            score += 2
        elif self.github_stars > 10:
            score += 1

        if self.npm_weekly_downloads > 100000 or self.pypi_weekly_downloads > 100000:
            score += 3

        # === Recency (max 10 pts) ===
        if self.last_active:
            age = datetime.now(timezone.utc) - self.last_active
            if age < timedelta(hours=1):
                score += 10
            elif age < timedelta(days=1):
                score += 8
            elif age < timedelta(days=7):
                score += 5
            elif age < timedelta(days=30):
                score += 3
            else:
                score += 1
        elif self.github_last_commit_days > 0:
            if self.github_last_commit_days < 7:
                score += 7
            elif self.github_last_commit_days < 30:
                score += 4
            elif self.github_last_commit_days < 90:
                score += 2

        # === Engineering maturity (max 5 pts) ===
        if self.has_public_changelog:
            score += 1
        if self.has_public_roadmap:
            score += 1
        if self.has_rate_limiting:
            score += 1
        if self.has_versioning:
            score += 1
        if self.has_deprecation_policy:
            score += 1

        return min(max(score, 0.0), 100.0)


@dataclass
class SolvencySignals:
    """
    Signals used to assess Solvency Risk.
    Answers: "Can this entity honor its financial commitments?"

    For registered entities: payment data from Stripe.
    For unregistered entities: inferred from public financials.
    """
    # Internal (registered entities)
    has_payment_method: bool = False
    stripe_verified: bool = False
    subscription_active: bool = False
    subscription_tier: str = "free"
    account_age_days: int = 0
    payment_failures_30d: int = 0
    total_spend: float = 0.0

    # Public financial signals (for scoring anyone)
    is_publicly_traded: bool = False
    market_cap_usd: float = 0.0
    has_public_financials: bool = False
    revenue_growing: bool = False
    profitable: bool = False
    funding_total_usd: float = 0.0
    funding_rounds: int = 0
    last_funding_date: Optional[str] = None
    employee_count: int = 0
    employee_count_growing: bool = False

    # Web3 / crypto signals
    has_treasury: bool = False
    treasury_value_usd: float = 0.0
    token_market_cap: float = 0.0

    # Risk signals
    has_pending_lawsuits: bool = False
    has_bankruptcy_filing: bool = False
    bbb_rating: str = ""                   # Better Business Bureau
    dun_bradstreet_score: int = 0

    def calculate(self) -> float:
        """Calculate solvency sub-score (0-100)."""
        score = 0.0

        # === Internal payment verification (max 30 pts) ===
        if self.has_payment_method:
            score += 10
        if self.stripe_verified:
            score += 10
        if self.subscription_active:
            score += 5
        tier_scores = {"free": 0, "pro": 3, "business": 4, "enterprise": 5}
        score += tier_scores.get(self.subscription_tier, 0)

        # === Account maturity (max 12 pts) ===
        if self.account_age_days > 730:
            score += 12
        elif self.account_age_days > 365:
            score += 9
        elif self.account_age_days > 180:
            score += 6
        elif self.account_age_days > 90:
            score += 4
        elif self.account_age_days > 30:
            score += 2

        # === Payment reliability (max 10 pts) ===
        if self.payment_failures_30d == 0 and self.total_spend > 0:
            score += 7
            if self.total_spend > 1000:
                score += 3
            elif self.total_spend > 100:
                score += 1
        elif self.payment_failures_30d <= 1:
            score += 4

        # === Public financial signals (max 30 pts) ===
        # These allow scoring entities we have NO internal data on
        if self.is_publicly_traded:
            score += 10
            if self.market_cap_usd > 10_000_000_000:     # $10B+
                score += 5
            elif self.market_cap_usd > 1_000_000_000:    # $1B+
                score += 3
        elif self.has_public_financials:
            score += 5

        if self.profitable:
            score += 4
        if self.revenue_growing:
            score += 3

        if self.funding_total_usd > 100_000_000:
            score += 5
        elif self.funding_total_usd > 10_000_000:
            score += 3
        elif self.funding_total_usd > 1_000_000:
            score += 2
        elif self.funding_rounds > 0:
            score += 1

        # === Size signals (max 10 pts) ===
        if self.employee_count > 10000:
            score += 6
        elif self.employee_count > 1000:
            score += 4
        elif self.employee_count > 100:
            score += 2
        elif self.employee_count > 10:
            score += 1

        if self.employee_count_growing:
            score += 2
        if self.dun_bradstreet_score > 80:
            score += 2

        # === Negative signals (penalties) ===
        if self.has_bankruptcy_filing:
            score -= 30
        if self.has_pending_lawsuits:
            score -= 10

        return min(max(score, 0.0), 100.0)


@dataclass
class ReputationSignals:
    """
    Signals used to assess Reputation Risk.
    Answers: "What does the world think of this entity?"

    This is the OPEN WEB pillar — it's what lets us score anyone on Earth.
    James Rausch's key insight: reputation is a public good.
    """
    # Sentiment analysis
    overall_sentiment: float = 0.0          # -1.0 (toxic) to 1.0 (glowing)
    sentiment_sample_size: int = 0
    sentiment_trend: str = "stable"         # improving, stable, declining

    # News & media
    news_mentions_30d: int = 0
    news_sentiment: float = 0.0
    has_negative_press: bool = False
    has_positive_press: bool = False

    # Review platforms
    google_reviews_count: int = 0
    google_reviews_rating: float = 0.0
    yelp_rating: float = 0.0
    glassdoor_rating: float = 0.0

    # Social signals
    twitter_followers: int = 0
    twitter_engagement_rate: float = 0.0
    reddit_mentions: int = 0
    reddit_sentiment: float = 0.0

    # Trust signals
    has_bbb_accreditation: bool = False
    has_trust_seals: bool = False           # e.g., Norton, McAfee, TRUSTe
    has_soc2: bool = False
    has_iso27001: bool = False
    has_gdpr_compliance: bool = False
    has_hipaa_compliance: bool = False

    # Community signals
    community_reports_positive: int = 0
    community_reports_negative: int = 0
    community_reports_fraud: int = 0

    # Blocklists
    on_spam_blocklist: bool = False
    on_fraud_blocklist: bool = False
    on_sanctions_list: bool = False

    def calculate(self) -> float:
        """Calculate reputation sub-score (0-100)."""
        score = 50.0  # Start at neutral (important for unknown entities)

        # === Sentiment (±20 pts from neutral) ===
        if self.sentiment_sample_size > 0:
            # Scale sentiment (-1 to 1) to (-20 to +20)
            sentiment_pts = self.overall_sentiment * 20
            score += sentiment_pts

            # Volume bonus
            if self.sentiment_sample_size > 1000:
                score += 3
            elif self.sentiment_sample_size > 100:
                score += 1

        # === Review platforms (max 15 pts) ===
        review_score = 0.0
        if self.google_reviews_rating > 0:
            review_score += (self.google_reviews_rating / 5.0) * 6
            if self.google_reviews_count > 100:
                review_score += 2
        if self.glassdoor_rating > 0:
            review_score += (self.glassdoor_rating / 5.0) * 4
        if self.yelp_rating > 0:
            review_score += (self.yelp_rating / 5.0) * 3
        score += min(review_score, 15)

        # === Trust certifications (max 15 pts) ===
        if self.has_soc2:
            score += 5
        if self.has_iso27001:
            score += 4
        if self.has_gdpr_compliance:
            score += 3
        if self.has_hipaa_compliance:
            score += 2
        if self.has_bbb_accreditation:
            score += 1

        # === Social credibility (max 8 pts) ===
        if self.twitter_followers > 100000:
            score += 4
        elif self.twitter_followers > 10000:
            score += 2
        elif self.twitter_followers > 1000:
            score += 1

        if self.twitter_engagement_rate > 0.03:
            score += 2
        if self.reddit_sentiment > 0.3 and self.reddit_mentions > 10:
            score += 2

        # === News presence (±8 pts) ===
        if self.has_positive_press:
            score += 4
        if self.has_negative_press:
            score -= 6

        if self.news_mentions_30d > 50:
            score += 2
        elif self.news_mentions_30d > 10:
            score += 1

        # Trend adjustment
        if self.sentiment_trend == "improving":
            score += 3
        elif self.sentiment_trend == "declining":
            score -= 5

        # === CRITICAL PENALTIES ===
        if self.on_sanctions_list:
            score = 0  # Automatic zero
        if self.on_fraud_blocklist:
            score -= 40
        if self.on_spam_blocklist:
            score -= 15
        if self.community_reports_fraud > 5:
            score -= 20
        elif self.community_reports_fraud > 0:
            score -= 10

        return min(max(score, 0.0), 100.0)


@dataclass
class NetworkSignals:
    """
    Signals used to assess Network Risk.
    Answers: "Who is this entity connected to? Who vouches for them?"

    James Rausch's graph-native insight: trust is transitive.
    If trusted entities vouch for you, your score improves.
    If you associate with bad actors, your score drops.
    """
    # Direct connections
    verified_partners_count: int = 0
    high_trust_connections: int = 0         # Connected entities with score > 700
    low_trust_connections: int = 0          # Connected entities with score < 400
    flagged_connections: int = 0            # Connected to known bad actors

    # Endorsements
    endorsements_received: int = 0
    endorsement_avg_score: float = 0.0     # Avg trust score of endorsers
    endorsements_given: int = 0

    # Integration signals (who uses / is used by)
    integration_partners: int = 0           # APIs, SDKs, platforms that integrate
    notable_customers: int = 0
    marketplace_presence: int = 0           # Listed on how many marketplaces

    # Graph metrics
    pagerank_score: float = 0.0            # PageRank within our trust graph
    clustering_coefficient: float = 0.0
    is_bridge_entity: bool = False          # Connects otherwise disconnected clusters

    def calculate(self) -> float:
        """Calculate network sub-score (0-100)."""
        score = 30.0  # Start at low-neutral (no connections = low trust)

        # === Vouching network (max 30 pts) ===
        if self.high_trust_connections > 10:
            score += 20
        elif self.high_trust_connections > 5:
            score += 15
        elif self.high_trust_connections > 2:
            score += 10
        elif self.high_trust_connections > 0:
            score += 5

        if self.verified_partners_count > 5:
            score += 10
        elif self.verified_partners_count > 0:
            score += 5

        # === Endorsements (max 15 pts) ===
        if self.endorsements_received > 0:
            endorsement_quality = min(self.endorsement_avg_score / 1000, 1.0)
            score += endorsement_quality * 10
            if self.endorsements_received > 10:
                score += 5
            elif self.endorsements_received > 3:
                score += 3

        # === Integration depth (max 15 pts) ===
        if self.integration_partners > 20:
            score += 10
        elif self.integration_partners > 5:
            score += 6
        elif self.integration_partners > 0:
            score += 3

        if self.notable_customers > 5:
            score += 5
        elif self.notable_customers > 0:
            score += 2

        # === Graph position (max 10 pts) ===
        if self.pagerank_score > 0.01:
            score += 5
        elif self.pagerank_score > 0.001:
            score += 3

        if self.is_bridge_entity:
            score += 3

        if self.marketplace_presence > 3:
            score += 2

        # === PENALTIES ===
        if self.flagged_connections > 3:
            score -= 20
        elif self.flagged_connections > 0:
            score -= 10

        if self.low_trust_connections > self.high_trust_connections * 2:
            score -= 10

        return min(max(score, 0.0), 100.0)


# =============================================
# THE TRUST SCORE — Final output
# =============================================

@dataclass
class TrustScore:
    """
    The final Trust Score output.
    Market2Agent Universal Trust Score — James Rausch's Trust Score Revolution.
    """
    score: int                        # 0-1000
    grade: TrustGrade
    risk_level: RiskLevel
    recommendation: Recommendation

    # Sub-scores (0-100 each)
    identity_score: float
    competence_score: float
    solvency_score: float
    reputation_score: float
    network_score: float

    # Breakdown for API consumers
    identity_signals: Dict[str, Any]
    competence_signals: Dict[str, Any]
    solvency_signals: Dict[str, Any]
    reputation_signals: Dict[str, Any]
    network_signals: Dict[str, Any]

    # Entity metadata
    entity_id: str
    entity_name: str
    entity_type: str
    is_verified: bool
    is_registered: bool                # In our registry or scored from public data

    # Score metadata
    calculated_at: str
    confidence: float                  # 0-1 (how much data we have)
    data_freshness: str                # "live", "cached", "stale"
    data_sources: List[str]            # Where we got the data
    signal_count: int                  # Total number of signals evaluated

    # James Rausch's attribution
    engine_version: str = "2.0.0"
    engine_author: str = "James Rausch — Lead Visionary, Market2Agent"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "grade": self.grade.value,
            "risk_level": self.risk_level.value,
            "recommendation": self.recommendation.value,
            "identity_score": round(self.identity_score, 1),
            "competence_score": round(self.competence_score, 1),
            "solvency_score": round(self.solvency_score, 1),
            "reputation_score": round(self.reputation_score, 1),
            "network_score": round(self.network_score, 1),
            "entity_id": self.entity_id,
            "entity_name": self.entity_name,
            "entity_type": self.entity_type,
            "is_verified": self.is_verified,
            "is_registered": self.is_registered,
            "calculated_at": self.calculated_at,
            "confidence": round(self.confidence, 2),
            "data_freshness": self.data_freshness,
            "data_sources": self.data_sources,
            "signal_count": self.signal_count,
            "engine_version": self.engine_version,
            "engine_author": self.engine_author,
        }

    def to_compact(self) -> Dict[str, Any]:
        """Minimal response for high-volume API calls."""
        return {
            "score": self.score,
            "grade": self.grade.value,
            "risk_level": self.risk_level.value,
            "recommendation": self.recommendation.value,
            "is_verified": self.is_verified,
            "confidence": round(self.confidence, 2),
        }

    def to_full(self) -> Dict[str, Any]:
        """Full response with all signal breakdowns."""
        d = self.to_dict()
        d["signals"] = {
            "identity": self.identity_signals,
            "competence": self.competence_signals,
            "solvency": self.solvency_signals,
            "reputation": self.reputation_signals,
            "network": self.network_signals,
        }
        return d


# =============================================
# THE SCORING FUNCTION — The heart of Market2Agent
# =============================================

def calculate_trust_score(
    entity_id: str,
    entity_name: str,
    entity_type: str = "unknown",
    is_verified: bool = False,
    is_registered: bool = False,
    identity: Optional[IdentitySignals] = None,
    competence: Optional[CompetenceSignals] = None,
    solvency: Optional[SolvencySignals] = None,
    reputation: Optional[ReputationSignals] = None,
    network: Optional[NetworkSignals] = None,
    data_freshness: str = "live",
    data_sources: Optional[List[str]] = None,
) -> TrustScore:
    """
    Calculate the composite Trust Score.
    James Rausch's Universal Trust Score — scores any entity on Earth.

    Weights (v2.0 — expanded from v1.0 by James Rausch):
        Identity:    30% (are you real?)
        Competence:  25% (can you deliver?)
        Reputation:  20% (what does the world say?)
        Solvency:    15% (can you pay?)
        Network:     10% (who vouches for you?)
    """
    # Default empty signals if not provided
    identity = identity or IdentitySignals()
    competence = competence or CompetenceSignals()
    solvency = solvency or SolvencySignals()
    reputation = reputation or ReputationSignals()
    network = network or NetworkSignals()

    # Calculate sub-scores
    id_score = identity.calculate()
    comp_score = competence.calculate()
    solv_score = solvency.calculate()
    rep_score = reputation.calculate()
    net_score = network.calculate()

    # Weighted composite (0-100)
    composite = (
        id_score   * 0.30 +
        comp_score * 0.25 +
        solv_score * 0.15 +
        rep_score  * 0.20 +
        net_score  * 0.10
    )

    # Scale to 0-1000
    score_1000 = int(composite * 10)

    # Grade (expanded scale)
    if score_1000 >= 900:
        grade = TrustGrade.AAA
    elif score_1000 >= 800:
        grade = TrustGrade.AA
    elif score_1000 >= 700:
        grade = TrustGrade.A
    elif score_1000 >= 600:
        grade = TrustGrade.BBB
    elif score_1000 >= 500:
        grade = TrustGrade.BB
    elif score_1000 >= 400:
        grade = TrustGrade.B
    elif score_1000 >= 200:
        grade = TrustGrade.CCC
    else:
        grade = TrustGrade.D

    # Risk level
    if score_1000 >= 850:
        risk = RiskLevel.MINIMAL
    elif score_1000 >= 700:
        risk = RiskLevel.LOW
    elif score_1000 >= 550:
        risk = RiskLevel.MODERATE
    elif score_1000 >= 400:
        risk = RiskLevel.ELEVATED
    elif score_1000 >= 250:
        risk = RiskLevel.HIGH
    elif score_1000 >= 100:
        risk = RiskLevel.SEVERE
    else:
        risk = RiskLevel.CRITICAL

    # Recommendation
    if score_1000 >= 700:
        rec = Recommendation.PROCEED
    elif score_1000 >= 550:
        rec = Recommendation.PROCEED_WITH_CAUTION
    elif score_1000 >= 400:
        rec = Recommendation.MANUAL_REVIEW
    elif score_1000 >= 200:
        rec = Recommendation.ENHANCED_DUE_DILIGENCE
    else:
        rec = Recommendation.REJECT

    # Confidence — based on how many data signals we actually have
    data_points = _count_data_points(identity, competence, solvency, reputation, network, is_verified, is_registered)
    signal_count = data_points["total"]
    confidence = min(data_points["total"] / data_points["max_possible"], 1.0)

    # Confidence penalty for unregistered entities
    if not is_registered:
        confidence *= 0.7  # We inherently trust our own data more

    return TrustScore(
        score=score_1000,
        grade=grade,
        risk_level=risk,
        recommendation=rec,
        identity_score=id_score,
        competence_score=comp_score,
        solvency_score=solv_score,
        reputation_score=rep_score,
        network_score=net_score,
        identity_signals={
            "domain_verified": identity.domain_verified,
            "email_verified": identity.email_verified,
            "structured_data": identity.has_structured_data,
            "knowledge_graph": identity.has_wikidata_entry or identity.has_wikipedia_page,
            "business_registration": identity.has_business_registration,
            "social_profiles": identity.social_profiles_count,
            "domain_age_days": identity.domain_age_days,
            "agent_card": identity.has_agent_card,
        },
        competence_signals={
            "total_transactions": competence.total_transactions,
            "success_rate": (
                round(competence.successful_transactions / competence.total_transactions * 100, 1)
                if competence.total_transactions > 0 else None
            ),
            "uptime_pct": competence.uptime_pct or None,
            "public_rating": competence.g2_rating or competence.trustpilot_rating or None,
            "github_stars": competence.github_stars or None,
            "has_status_page": competence.has_status_page,
        },
        solvency_signals={
            "payment_verified": solvency.stripe_verified,
            "subscription_active": solvency.subscription_active,
            "tier": solvency.subscription_tier,
            "publicly_traded": solvency.is_publicly_traded,
            "funding_total_usd": solvency.funding_total_usd or None,
            "employee_count": solvency.employee_count or None,
        },
        reputation_signals={
            "overall_sentiment": round(reputation.overall_sentiment, 2) if reputation.sentiment_sample_size > 0 else None,
            "sentiment_trend": reputation.sentiment_trend,
            "google_reviews_rating": reputation.google_reviews_rating or None,
            "has_trust_certifications": reputation.has_soc2 or reputation.has_iso27001,
            "on_blocklist": reputation.on_fraud_blocklist or reputation.on_spam_blocklist,
            "community_fraud_reports": reputation.community_reports_fraud,
        },
        network_signals={
            "high_trust_connections": network.high_trust_connections,
            "verified_partners": network.verified_partners_count,
            "endorsements": network.endorsements_received,
            "integration_partners": network.integration_partners,
            "flagged_connections": network.flagged_connections,
        },
        entity_id=entity_id,
        entity_name=entity_name,
        entity_type=entity_type,
        is_verified=is_verified,
        is_registered=is_registered,
        calculated_at=datetime.now(timezone.utc).isoformat(),
        confidence=confidence,
        data_freshness=data_freshness,
        data_sources=data_sources or ["registry"] if is_registered else ["public_web"],
        signal_count=signal_count,
    )


def _count_data_points(
    identity: IdentitySignals,
    competence: CompetenceSignals,
    solvency: SolvencySignals,
    reputation: ReputationSignals,
    network: NetworkSignals,
    is_verified: bool,
    is_registered: bool,
) -> Dict[str, int]:
    """Count how many data points we actually have vs the maximum possible."""
    total = 0

    # Identity signals
    if is_verified: total += 1
    if is_registered: total += 1
    if identity.domain_verified or identity.dns_txt_verified or identity.file_verified: total += 1
    if identity.email_verified: total += 1
    if identity.has_structured_data: total += 1
    if identity.has_organization_schema: total += 1
    if identity.has_wikidata_entry or identity.has_wikipedia_page: total += 1
    if identity.has_business_registration: total += 1
    if identity.social_profiles_count > 0: total += 1
    if identity.domain_age_days > 0: total += 1
    if identity.ssl_valid: total += 1

    # Competence signals
    if competence.total_transactions > 0: total += 1
    if competence.uptime_pct > 0: total += 1
    if competence.avg_response_time_ms > 0 or competence.api_response_time_ms > 0: total += 1
    if competence.last_active: total += 1
    if competence.visibility_score > 0: total += 1
    if competence.g2_rating > 0 or competence.trustpilot_rating > 0: total += 1
    if competence.github_stars > 0: total += 1
    if competence.has_status_page: total += 1

    # Solvency signals
    if solvency.has_payment_method: total += 1
    if solvency.is_publicly_traded: total += 1
    if solvency.funding_total_usd > 0: total += 1
    if solvency.employee_count > 0: total += 1
    if solvency.account_age_days > 0: total += 1

    # Reputation signals
    if reputation.sentiment_sample_size > 0: total += 1
    if reputation.google_reviews_count > 0: total += 1
    if reputation.news_mentions_30d > 0: total += 1
    if reputation.has_soc2 or reputation.has_iso27001: total += 1
    if reputation.twitter_followers > 0: total += 1

    # Network signals
    if network.high_trust_connections > 0: total += 1
    if network.endorsements_received > 0: total += 1
    if network.integration_partners > 0: total += 1
    if network.pagerank_score > 0: total += 1

    return {
        "total": total,
        "max_possible": 35,
    }


# =============================================
# BACKWARD COMPATIBILITY — v1 wrapper
# =============================================

def calculate_trust_score_v1(
    entity_id: str,
    entity_name: str,
    is_verified: bool,
    identity: IdentitySignals,
    competence: CompetenceSignals,
    solvency: SolvencySignals,
    data_freshness: str = "live",
) -> TrustScore:
    """
    v1 compatibility wrapper.
    Maps the original 3-pillar call to the new 5-pillar engine.
    """
    return calculate_trust_score(
        entity_id=entity_id,
        entity_name=entity_name,
        is_verified=is_verified,
        is_registered=True,
        identity=identity,
        competence=competence,
        solvency=solvency,
        data_freshness=data_freshness,
    )
