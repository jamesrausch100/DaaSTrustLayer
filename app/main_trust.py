"""
Market2Agent — Unified Platform
GEO Visibility + Trust Scoring + Agent Optimization

The trust layer for the AI economy.
Built by James Rausch.

Start with:
    uvicorn app.main_trust:app --host 0.0.0.0 --port 8000
"""
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import structlog

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("platform_starting", version="2.0.0")

    # Initialize Neo4j schema
    try:
        from app.db.neo4j import init_schema
        init_schema()
        logger.info("neo4j_schema_initialized")
    except Exception as e:
        logger.warning("neo4j_init_failed", error=str(e))

    # Initialize compute pipeline
    try:
        from app.compute.pipeline import get_cache, get_persistence
        cache = get_cache()
        persistence = get_persistence()
        persistence.init_schema()
        logger.info("compute_pipeline_initialized",
                     cache_enabled=cache._enabled,
                     persistence_available=persistence._available)
    except Exception as e:
        logger.warning("compute_pipeline_init_failed", error=str(e))

    yield

    # Shutdown
    try:
        from app.compute.pipeline import shutdown as pipeline_shutdown
        pipeline_shutdown()
    except Exception:
        pass
    try:
        from app.db.neo4j import close
        close()
    except Exception:
        pass
    logger.info("platform_stopped")


app = FastAPI(
    title="Market2Agent — AI Visibility & Trust Platform",
    description=(
        "GEO visibility monitoring, universal trust scoring, and agent optimization. "
        "The trust layer for the AI economy. Built by James Rausch."
    ),
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    contact={
        "name": "James Rausch",
        "url": "https://market2agent.ai",
        "email": "hello@market2agent.ai",
    },
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://market2agent.ai",
        "https://www.market2agent.ai",
        "https://market2agent.com",
        "https://www.market2agent.com",
        "http://localhost:3000",
        "http://localhost:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "X-Request-Id",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "Retry-After",
    ],
)


@app.middleware("http")
async def request_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    start = time.time()
    request.state.request_id = request_id
    response = await call_next(request)
    duration_ms = round((time.time() - start) * 1000, 2)
    response.headers["X-Request-Id"] = request_id
    response.headers["X-Response-Time"] = f"{duration_ms}ms"
    if request.url.path not in ("/health", "/v1/trust/health"):
        logger.info("request",
                     method=request.method,
                     path=request.url.path,
                     status=response.status_code,
                     duration_ms=duration_ms,
                     request_id=request_id)
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception",
                 path=request.url.path,
                 error=str(exc),
                 type=type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": "Something went wrong. We've been notified.",
            "request_id": getattr(request.state, "request_id", "unknown"),
        },
    )


# === Load all routers (graceful — each can fail independently) ===

# Trust API (core scoring engine)
try:
    from app.api.trust import trust_router, keys_router, admin_trust_router
    app.include_router(trust_router)
    app.include_router(keys_router)
    app.include_router(admin_trust_router)
    logger.info("router_loaded", router="trust_api")
except Exception as e:
    logger.warning("router_failed", router="trust_api", error=str(e))

# Auth (Google OAuth)
try:
    from app.auth import router as auth_router
    app.include_router(auth_router)
    logger.info("router_loaded", router="auth")
except Exception as e:
    logger.warning("router_failed", router="auth", error=str(e))

# Entity Registry
try:
    from app.api.entities import public_router as entity_public_router
    from app.api.entities import user_router as entity_user_router
    app.include_router(entity_public_router)
    app.include_router(entity_user_router)
    logger.info("router_loaded", router="entities")
except Exception as e:
    logger.warning("router_failed", router="entities", error=str(e))

# Visibility Index
try:
    from app.api.visibility import router as visibility_router
    app.include_router(visibility_router)
    logger.info("router_loaded", router="visibility")
except Exception as e:
    logger.warning("router_failed", router="visibility", error=str(e))

# Dashboard
try:
    from app.api.dashboard import router as dashboard_router
    app.include_router(dashboard_router)
    logger.info("router_loaded", router="dashboard")
except Exception as e:
    logger.warning("router_failed", router="dashboard", error=str(e))

# Subscriptions (Stripe)
try:
    from app.api.subscriptions import router as subscriptions_router
    app.include_router(subscriptions_router)
    logger.info("router_loaded", router="subscriptions")
except Exception as e:
    logger.warning("router_failed", router="subscriptions", error=str(e))

# Agents
try:
    from app.api.agents import user_router as agents_user_router
    from app.api.agents import admin_router as agents_admin_router
    app.include_router(agents_user_router)
    app.include_router(agents_admin_router)
    logger.info("router_loaded", router="agents")
except Exception as e:
    logger.warning("router_failed", router="agents", error=str(e))

# Careers (intern applications + resume collection)
try:
    from app.api.careers import router as careers_router
    app.include_router(careers_router)
    logger.info("router_loaded", router="careers")
except Exception as e:
    logger.warning("router_failed", router="careers", error=str(e))

# Static files (frontend)
try:
    app.mount("/static", StaticFiles(directory="frontend"), name="static")
    logger.info("static_files_mounted")
except Exception as e:
    logger.warning("static_files_failed", error=str(e))


# === Core endpoints ===

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "market2agent-platform",
        "version": "2.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/")
async def root():
    return {
        "name": "Market2Agent",
        "tagline": "The trust layer for the AI economy",
        "version": "2.0.0",
        "products": {
            "geo_visibility": "Monitor your brand visibility in AI search engines",
            "trust_scoring": "Universal trust scoring for any entity on Earth",
            "agent_optimization": "AI agents that audit and improve your digital presence",
        },
        "endpoints": {
            "trust_score": "GET /v1/trust/score?target={anything}",
            "trust_preview": "GET /v1/trust/preview?target={entity} (free)",
            "trust_batch": "POST /v1/trust/batch",
            "trust_compare": "GET /v1/trust/compare?entity_a={a}&entity_b={b}",
            "entities": "GET /v1/entities/{slug}",
            "visibility": "GET /v1/visibility/{entity_id}",
            "health": "GET /health",
            "docs": "GET /docs",
        },
        "sdk": "pip install market2agent",
        "website": "https://market2agent.ai",
    }
