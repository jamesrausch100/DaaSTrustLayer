# Market2Agent — Deployment Guide

## What You're Deploying

One droplet running everything:
- **FastAPI app** (port 8000) — trust scoring, GEO, entities, auth
- **Redis** — caching + rate limiting
- **Neo4j** — entity registry + score persistence
- **Nginx** — serves website + proxies API
- **Certbot** — free SSL from Let's Encrypt

## Prerequisites

- A DigitalOcean droplet (Ubuntu 22.04/24.04, 4GB+ RAM recommended)
- Your domain (`market2agent.ai`) pointed at the droplet's IP
- SSH access to the droplet

## Step 1: SSH Into Your Droplet

```bash
ssh root@YOUR_DROPLET_IP
```

## Step 2: Install Docker

```bash
# Install Docker
curl -fsSL https://get.docker.com | sh

# Install Docker Compose plugin
apt-get install -y docker-compose-plugin

# Verify
docker --version
docker compose version
```

## Step 3: Upload the Project

From your local machine (where you unzipped the deliverable):

```bash
scp -r m2a/ root@YOUR_DROPLET_IP:/opt/market2agent
```

Or if you git-pushed it:

```bash
cd /opt
git clone https://github.com/YOUR_REPO/market2agent.git
cd market2agent
```

## Step 4: Configure Environment

```bash
cd /opt/market2agent
cp .env.example .env
nano .env
```

**Minimum required changes:**
```
NEO4J_PASSWORD=pick_something_strong_here
SECRET_KEY=run: python3 -c "import secrets; print(secrets.token_hex(32))"
```

**For Google OAuth (user login):**
1. Go to https://console.cloud.google.com/apis/credentials
2. Create OAuth 2.0 credentials
3. Set redirect URI to: `https://market2agent.ai/v1/auth/google/callback`
4. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env

**For Stripe (subscriptions):**
1. Get keys from https://dashboard.stripe.com/apikeys
2. Add STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY to .env

These are optional for initial deploy — the app starts without them.

## Step 5: Launch

```bash
cd /opt/market2agent
docker compose up -d
```

Watch the logs:
```bash
docker compose logs -f app
```

You should see:
```
platform_starting version=2.0.0
neo4j_schema_initialized
compute_pipeline_initialized
router_loaded router=trust_api
...
```

Some routers may show warnings if OAuth/Stripe aren't configured — that's fine.

## Step 6: Verify

```bash
# Health check
curl http://localhost:8000/health

# Root info
curl http://localhost:8000/

# Trust preview (no auth needed)
curl "http://localhost:8000/v1/trust/preview?target=google.com"

# API docs
# Open http://YOUR_DROPLET_IP/docs in browser
```

## Step 7: SSL Certificate

Point your domain at the droplet IP first (A record), then:

```bash
# Stop nginx temporarily
docker compose stop nginx

# Get certificate
docker run --rm -v m2a_certbot-etc:/etc/letsencrypt \
  -v m2a_certbot-var:/var/lib/letsencrypt \
  -v /opt/market2agent/frontend:/var/www/html \
  -p 80:80 certbot/certbot certonly \
  --standalone --agree-tos --no-eff-email \
  -d market2agent.ai -d www.market2agent.ai \
  -m jamesrausch100@gmail.com

# Uncomment the HTTPS server block in nginx.conf
# and add: return 301 https://$host$request_uri; to the HTTP block
nano nginx.conf

# Restart everything
docker compose up -d
```

## Step 8: Set Up Auto-Renewal

```bash
crontab -e
# Add:
0 3 * * * cd /opt/market2agent && docker compose run --rm certbot renew && docker compose restart nginx
```

## Common Commands

```bash
# View logs
docker compose logs -f app
docker compose logs -f neo4j

# Restart app after code changes
docker compose up -d --build app

# Full restart
docker compose down && docker compose up -d

# Neo4j browser (accessible from droplet only)
# http://localhost:7474

# Check what's running
docker compose ps

# Shell into the app container
docker compose exec app bash
```

## Architecture

```
Internet → Nginx (:80/:443)
              ├── /                → frontend/index.html (static)
              ├── /about.html     → frontend/about.html (static)
              ├── /v1/*           → FastAPI app (:8000)
              ├── /docs           → FastAPI Swagger UI
              └── /health         → FastAPI health check

FastAPI App
  ├── Trust API        /v1/trust/*     (scoring engine)
  ├── Entity Registry  /v1/entities/*  (claim + verify businesses)
  ├── Visibility       /v1/visibility/* (GEO monitoring)
  ├── Auth             /v1/auth/*      (Google OAuth)
  ├── Dashboard        /v1/user/*      (manage domains)
  ├── Subscriptions    /v1/*           (Stripe billing)
  └── Agents           /v1/agents/*    (automated audits)
         │                    │
         ▼                    ▼
       Redis              Neo4j
    (cache/rate)       (graph store)
```
