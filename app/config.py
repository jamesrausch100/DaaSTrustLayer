"""
Market2Agent Platform — Configuration
Unified config for GEO + Trust + Agents.

All settings load from environment variables with safe defaults for development.
In production, set M2A_ENV=production to enforce required values.
"""
import os
import secrets
from typing import List
from functools import lru_cache

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class Settings:
    def __init__(self):
        self.ENVIRONMENT = os.getenv("M2A_ENV", "development")

        # === Database ===
        self.NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
        self.NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "m2a_dev_password")
        self.REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

        # === Stripe ===
        self.STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
        self.STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
        self.STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
        self.STRIPE_PRICE_PRO = os.getenv("STRIPE_PRICE_PRO", "")
        self.STRIPE_PRICE_ENTERPRISE = os.getenv("STRIPE_PRICE_ENTERPRISE", "")

        # === Google OAuth ===
        self.GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
        self.GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

        # === AI APIs (for GEO visibility monitoring) ===
        self.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
        self.ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
        self.PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")
        self.GOOGLE_AI_API_KEY = os.getenv("GOOGLE_AI_API_KEY", "")

        # === Open Web Collectors (for trust scoring) ===
        self.GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
        self.GOOGLE_SAFE_BROWSING_KEY = os.getenv("GOOGLE_SAFE_BROWSING_KEY", "")
        self.NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")

        # === Email ===
        self.SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
        self.SMTP_USER = os.getenv("SMTP_USER", "")
        self.SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
        self.EMAIL_ENABLED = bool(self.SMTP_USER and self.SMTP_PASSWORD)

        # === Application ===
        self._secret_from_env = os.getenv("SECRET_KEY", "")
        if self._secret_from_env:
            self.SECRET_KEY = self._secret_from_env
        else:
            self.SECRET_KEY = secrets.token_hex(32)
            if self.ENVIRONMENT == "production":
                raise RuntimeError("SECRET_KEY must be set in production. Add it to .env")
            import warnings
            warnings.warn("SECRET_KEY not set — using random key. JWTs will not survive restarts.")

        self.APP_URL = os.getenv("APP_URL", "https://market2agent.ai")
        self.API_URL = os.getenv("API_URL", "https://api.market2agent.ai")
        self.M2A_HOST = os.getenv("M2A_HOST", "0.0.0.0")
        self.M2A_PORT = int(os.getenv("M2A_PORT", "8000"))
        self.M2A_WORKERS = int(os.getenv("M2A_WORKERS", "4"))

        self.M2A_ADMIN_KEY = os.getenv("M2A_ADMIN_KEY", "")
        if not self.M2A_ADMIN_KEY:
            if self.ENVIRONMENT == "production":
                raise RuntimeError("M2A_ADMIN_KEY must be set in production. Add it to .env")
            self.M2A_ADMIN_KEY = "admin_dev_key"

        # === Admin ===
        self.ADMIN_EMAILS = [
            e.strip().lower()
            for e in os.getenv("ADMIN_EMAILS", "jamesrausch100@gmail.com").split(",")
            if e.strip()
        ]

        # === Rate Limits ===
        self.RATE_LIMIT_FREE = int(os.getenv("RATE_LIMIT_FREE", "10"))
        self.RATE_LIMIT_BASIC = int(os.getenv("RATE_LIMIT_BASIC", "100"))
        self.RATE_LIMIT_PRO = int(os.getenv("RATE_LIMIT_PRO", "1000"))
        self.RATE_LIMIT_ENTERPRISE = int(os.getenv("RATE_LIMIT_ENTERPRISE", "10000"))

        # === Visibility Monitoring ===
        self.VISIBILITY_CHECK_INTERVAL = int(os.getenv("VISIBILITY_CHECK_INTERVAL", "24"))
        self.VISIBILITY_MAX_PROMPTS = int(os.getenv("VISIBILITY_MAX_PROMPTS", "20"))
        self.VISIBILITY_SYSTEMS = os.getenv("VISIBILITY_SYSTEMS", "chatgpt,claude,perplexity,gemini").split(",")

        # === Security ===
        self.JWT_EXPIRY_DAYS = int(os.getenv("JWT_EXPIRY_DAYS", "30"))
        self.COOKIE_SECURE = self.is_production
        self.COOKIE_DOMAIN = os.getenv("COOKIE_DOMAIN", ".market2agent.ai")

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def visibility_enabled(self) -> bool:
        return any([
            self.OPENAI_API_KEY,
            self.ANTHROPIC_API_KEY,
            self.PERPLEXITY_API_KEY,
            self.GOOGLE_AI_API_KEY,
        ])


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


# === Pricing Tiers ===
PRICING_TIERS = {
    "free": {
        "name": "Free",
        "price": 0,
        "entities": 1,
        "competitors": 0,
        "visibility_monitoring": False,
        "agent_deployment": False,
        "api_access": True,
        "trust_checks_per_day": 100,
    },
    "pro": {
        "name": "Pro",
        "price": 49,
        "stripe_price_key": "STRIPE_PRICE_PRO",
        "entities": 5,
        "competitors": 3,
        "visibility_monitoring": True,
        "visibility_frequency": "daily",
        "agent_deployment": False,
        "api_access": True,
        "trust_checks_per_day": 10000,
    },
    "enterprise": {
        "name": "Enterprise",
        "price": 299,
        "stripe_price_key": "STRIPE_PRICE_ENTERPRISE",
        "entities": "unlimited",
        "competitors": "unlimited",
        "visibility_monitoring": True,
        "visibility_frequency": "real-time",
        "agent_deployment": True,
        "api_access": True,
        "trust_checks_per_day": "unlimited",
    },
}


def get_tier_limits(tier: str) -> dict:
    return PRICING_TIERS.get(tier, PRICING_TIERS["free"])
