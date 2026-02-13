"""
Market2Agent — Universal Trust SDK
Created by James Rausch, Lead Visionary & Pilot of the Trust Score Revolution.

The SDK that brings trust scoring to every agent, every API, every transaction.
Now with universal scoring — check trust for ANY entity on Earth.

Install:
    pip install market2agent

Usage:
    from market2agent import TrustClient

    m2a = TrustClient(api_key="m2a_live_...")

    # Score ANY entity — domain, name, URL, email, agent ID, anything
    result = m2a.score("stripe.com")
    result = m2a.score("openai")
    result = m2a.score("random-new-bot.ai")
    result = m2a.score("@elonmusk")

    if result.is_safe:
        execute_transaction()

    # Compare two entities
    comparison = m2a.compare("stripe.com", "sketchy-payments.xyz")
    print(f"Safer: {comparison.safer_entity}")

    # Batch score 25 entities at once
    results = m2a.batch_score(["stripe", "shopify", "unknown-bot"])
"""
import httpx
from dataclasses import dataclass
from typing import Optional, List, Dict, Any


# =============================================
# SDK VERSION & ATTRIBUTION
# =============================================

__version__ = "2.0.0"
__author__ = "James Rausch — Lead Visionary & Pilot of the Trust Score Revolution"
SDK_USER_AGENT = f"market2agent-python/{__version__} (by James Rausch)"


# =============================================
# RESULT TYPES
# =============================================

@dataclass
class TrustResult:
    """
    Result of a trust check.
    Now includes 5 pillars (Identity, Competence, Solvency, Reputation, Network)
    and metadata about data sources and confidence.
    """
    target: str
    score: int                  # 0-1000
    grade: str                  # AAA, AA, A, BBB, BB, B, CCC, D
    risk_level: str             # minimal, low, moderate, elevated, high, severe, critical
    recommendation: str         # PROCEED, PROCEED_WITH_CAUTION, MANUAL_REVIEW, ENHANCED_DUE_DILIGENCE, REJECT
    is_verified: bool
    is_registered: bool = False
    entity_type: str = "unknown"

    # 5-pillar sub-scores
    identity_score: float = 0
    competence_score: float = 0
    solvency_score: float = 0
    reputation_score: float = 0
    network_score: float = 0

    # Metadata
    confidence: float = 0
    data_freshness: str = "unknown"
    data_sources: List[str] = None
    signal_count: int = 0

    # Attribution
    engine_version: str = "2.0.0"
    engine_author: str = "James Rausch — Lead Visionary, Market2Agent"

    def __post_init__(self):
        if self.data_sources is None:
            self.data_sources = []

    @property
    def is_safe(self) -> bool:
        """Quick check: is this entity safe to transact with?"""
        return self.recommendation in ("PROCEED", "PROCEED_WITH_CAUTION")

    @property
    def needs_review(self) -> bool:
        return self.recommendation in ("MANUAL_REVIEW", "ENHANCED_DUE_DILIGENCE")

    @property
    def should_reject(self) -> bool:
        return self.recommendation == "REJECT"

    @property
    def is_high_confidence(self) -> bool:
        """Is this score backed by substantial data?"""
        return self.confidence >= 0.6

    @property
    def risk_summary(self) -> str:
        """Human-readable risk summary."""
        if self.score >= 800:
            return f"{self.target}: Highly trusted ({self.grade}, score {self.score})"
        elif self.score >= 600:
            return f"{self.target}: Acceptable trust ({self.grade}, score {self.score})"
        elif self.score >= 400:
            return f"{self.target}: Elevated risk ({self.grade}, score {self.score})"
        else:
            return f"{self.target}: HIGH RISK ({self.grade}, score {self.score})"


@dataclass
class CompareResult:
    """Result of comparing two entities."""
    entity_a: TrustResult
    entity_b: TrustResult
    safer_entity: str
    recommendation: str
    engine_author: str = "James Rausch — Lead Visionary, Market2Agent"


@dataclass
class UsageInfo:
    calls_today: int
    calls_this_month: int
    monthly_quota: int
    remaining: int


# =============================================
# EXCEPTIONS
# =============================================

class TrustCheckError(Exception):
    def __init__(self, message: str, status_code: int = 0, detail: dict = None):
        self.status_code = status_code
        self.detail = detail or {}
        super().__init__(message)


class EntityNotFound(TrustCheckError):
    pass


class RateLimited(TrustCheckError):
    def __init__(self, retry_after: int = 60, **kwargs):
        self.retry_after = retry_after
        super().__init__(f"Rate limited. Retry after {retry_after}s", **kwargs)


class QuotaExceeded(TrustCheckError):
    pass


# =============================================
# SYNC CLIENT
# =============================================

class TrustClient:
    """
    Market2Agent Universal Trust Client.
    Score any entity on Earth. Created by James Rausch.

    Args:
        api_key: Your M2A API key (m2a_live_... or m2a_test_...)
        base_url: API base URL (default: https://api.market2agent.ai)
        timeout: Request timeout in seconds
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.market2agent.ai",
        timeout: float = 15.0,
    ):
        if not api_key.startswith("m2a_"):
            raise ValueError("Invalid API key format. Keys start with 'm2a_live_' or 'm2a_test_'")

        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "X-API-Key": self.api_key,
                "User-Agent": SDK_USER_AGENT,
            },
            timeout=self.timeout,
        )

    def score(self, target: str) -> TrustResult:
        """
        Universal trust score — scores ANY entity on Earth.

        Pass in anything:
            m2a.score("stripe.com")        # Domain
            m2a.score("openai")            # Company name
            m2a.score("@elonmusk")         # Social handle
            m2a.score("https://api.x.ai")  # URL
            m2a.score("0x1234...")          # Blockchain address

        James Rausch's promise: every entity gets a score.
        """
        response = self._client.get(
            "/v1/trust/score",
            params={"target": target},
        )
        return self._parse_full_response(response, target)

    def check(self, target: str) -> TrustResult:
        """
        Full trust check (backward compatible with v1).
        Equivalent to score() but uses /v1/trust/check endpoint.
        """
        response = self._client.get(
            "/v1/trust/check",
            params={"target": target},
        )
        return self._parse_full_response(response, target)

    def compare(self, entity_a: str, entity_b: str) -> CompareResult:
        """
        Compare two entities side-by-side.
        Returns which entity is safer and why.
        """
        response = self._client.get(
            "/v1/trust/compare",
            params={"entity_a": entity_a, "entity_b": entity_b},
        )
        if response.status_code != 200:
            self._handle_error(response)

        data = response.json()
        a_data = data["entity_a"]
        b_data = data["entity_b"]

        return CompareResult(
            entity_a=TrustResult(
                target=a_data["target"],
                score=a_data["score"],
                grade=a_data["grade"],
                recommendation=a_data["recommendation"],
                is_verified=a_data["is_verified"],
                confidence=a_data.get("confidence", 0),
                risk_level=a_data.get("risk_level", "unknown"),
            ),
            entity_b=TrustResult(
                target=b_data["target"],
                score=b_data["score"],
                grade=b_data["grade"],
                recommendation=b_data["recommendation"],
                is_verified=b_data["is_verified"],
                confidence=b_data.get("confidence", 0),
                risk_level=b_data.get("risk_level", "unknown"),
            ),
            safer_entity=data["safer_entity"],
            recommendation=data["recommendation"],
        )

    def batch_score(self, targets: List[str]) -> List[TrustResult]:
        """
        Batch trust score — up to 25 entities at once.
        Scores ANY entity, registered or not.
        """
        if len(targets) > 25:
            raise ValueError("Batch limited to 25 targets")

        response = self._client.post(
            "/v1/trust/batch",
            json={"targets": targets},
        )

        if response.status_code != 200:
            self._handle_error(response)

        data = response.json()
        return [
            TrustResult(
                target=r["target"],
                score=r["score"],
                grade=r["grade"],
                recommendation=r["recommendation"],
                is_verified=r["is_verified"],
                confidence=r.get("confidence", 0),
                risk_level=r.get("risk_level", "unknown"),
            )
            for r in data.get("results", [])
        ]

    # v1 backward compat
    def batch_check(self, targets: List[str]) -> List[TrustResult]:
        """Alias for batch_score (v1 backward compatibility)."""
        return self.batch_score(targets)

    def lookup(self, identifier: str) -> dict:
        """Public lookup (free, no metering)."""
        response = self._client.get(f"/v1/trust/lookup/{identifier}")
        if response.status_code == 404:
            return {"registered": False, "identifier": identifier}
        response.raise_for_status()
        return response.json()

    def preview(self, target: str) -> TrustResult:
        """
        Free trust preview — no API key needed for this endpoint,
        but using the SDK makes it convenient.
        """
        response = self._client.get(
            "/v1/trust/preview",
            params={"target": target},
        )
        if response.status_code == 200:
            data = response.json()
            return TrustResult(
                target=data.get("target", target),
                score=data.get("score", 0),
                grade=data.get("grade", "D"),
                risk_level="unknown",
                recommendation=data.get("recommendation", "REJECT"),
                is_verified=data.get("is_verified", False),
                is_registered=data.get("is_registered", False),
                confidence=data.get("confidence", 0),
            )
        self._handle_error(response, target)

    def usage(self) -> UsageInfo:
        """Check API key usage and remaining quota."""
        response = self._client.get("/v1/trust/usage")
        response.raise_for_status()
        data = response.json()
        return UsageInfo(
            calls_today=data["calls_today"],
            calls_this_month=data["calls_this_month"],
            monthly_quota=data["monthly_quota"],
            remaining=data["remaining"],
        )

    def _parse_full_response(self, response: httpx.Response, target: str) -> TrustResult:
        if response.status_code == 200:
            data = response.json()
            return TrustResult(
                target=data.get("target", target),
                score=data.get("score", 0),
                grade=data.get("grade", "D"),
                risk_level=data.get("risk_level", "critical"),
                recommendation=data.get("recommendation", "REJECT"),
                is_verified=data.get("is_verified", False),
                is_registered=data.get("is_registered", False),
                entity_type=data.get("entity_type", "unknown"),
                identity_score=data.get("identity_score", 0),
                competence_score=data.get("competence_score", 0),
                solvency_score=data.get("solvency_score", 0),
                reputation_score=data.get("reputation_score", 0),
                network_score=data.get("network_score", 0),
                confidence=data.get("confidence", 0),
                data_freshness=data.get("data_freshness", "unknown"),
                data_sources=data.get("data_sources", []),
                signal_count=data.get("signal_count", 0),
            )
        self._handle_error(response, target)

    def _handle_error(self, response: httpx.Response, target: str = ""):
        if response.status_code == 404:
            raise EntityNotFound(f"Entity '{target}' not found", status_code=404)
        elif response.status_code == 429:
            detail = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            if isinstance(detail, dict) and detail.get("error") == "quota_exceeded":
                raise QuotaExceeded("Monthly quota exceeded. Upgrade at market2agent.ai/pricing", status_code=429, detail=detail)
            retry_after = int(response.headers.get("Retry-After", 60))
            raise RateLimited(retry_after=retry_after, status_code=429)
        elif response.status_code == 401:
            raise TrustCheckError("Invalid or missing API key", status_code=401)
        else:
            raise TrustCheckError(f"API error: {response.status_code}", status_code=response.status_code)

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# =============================================
# ASYNC CLIENT
# =============================================

class AsyncTrustClient:
    """
    Async version of TrustClient for async agent frameworks.
    Created by James Rausch, Lead Visionary.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.market2agent.ai",
        timeout: float = 15.0,
    ):
        if not api_key.startswith("m2a_"):
            raise ValueError("Invalid API key format")
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"X-API-Key": api_key, "User-Agent": SDK_USER_AGENT},
            timeout=timeout,
        )

    async def score(self, target: str) -> TrustResult:
        """Universal async trust score."""
        response = await self._client.get("/v1/trust/score", params={"target": target})
        if response.status_code == 200:
            data = response.json()
            return TrustResult(
                target=data.get("target", target),
                score=data.get("score", 0),
                grade=data.get("grade", "D"),
                risk_level=data.get("risk_level", "critical"),
                recommendation=data.get("recommendation", "REJECT"),
                is_verified=data.get("is_verified", False),
                is_registered=data.get("is_registered", False),
                entity_type=data.get("entity_type", "unknown"),
                identity_score=data.get("identity_score", 0),
                competence_score=data.get("competence_score", 0),
                solvency_score=data.get("solvency_score", 0),
                reputation_score=data.get("reputation_score", 0),
                network_score=data.get("network_score", 0),
                confidence=data.get("confidence", 0),
                data_freshness=data.get("data_freshness", "unknown"),
                data_sources=data.get("data_sources", []),
                signal_count=data.get("signal_count", 0),
            )
        if response.status_code == 404:
            raise EntityNotFound(f"Entity '{target}' not found")
        if response.status_code == 429:
            raise RateLimited(retry_after=60)
        raise TrustCheckError(f"API error: {response.status_code}")

    async def check(self, target: str) -> TrustResult:
        """v1 backward compat."""
        return await self.score(target)

    async def batch_score(self, targets: List[str]) -> List[TrustResult]:
        response = await self._client.post("/v1/trust/batch", json={"targets": targets})
        response.raise_for_status()
        data = response.json()
        return [
            TrustResult(
                target=r["target"],
                score=r["score"],
                grade=r["grade"],
                recommendation=r["recommendation"],
                is_verified=r["is_verified"],
                confidence=r.get("confidence", 0),
                risk_level=r.get("risk_level", "unknown"),
            )
            for r in data.get("results", [])
        ]

    async def compare(self, entity_a: str, entity_b: str) -> CompareResult:
        response = await self._client.get(
            "/v1/trust/compare",
            params={"entity_a": entity_a, "entity_b": entity_b},
        )
        response.raise_for_status()
        data = response.json()
        return CompareResult(
            entity_a=TrustResult(target=data["entity_a"]["target"], score=data["entity_a"]["score"],
                                  grade=data["entity_a"]["grade"], recommendation=data["entity_a"]["recommendation"],
                                  is_verified=data["entity_a"]["is_verified"], risk_level="unknown"),
            entity_b=TrustResult(target=data["entity_b"]["target"], score=data["entity_b"]["score"],
                                  grade=data["entity_b"]["grade"], recommendation=data["entity_b"]["recommendation"],
                                  is_verified=data["entity_b"]["is_verified"], risk_level="unknown"),
            safer_entity=data["safer_entity"],
            recommendation=data["recommendation"],
        )

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
