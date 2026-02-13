# Market2Agent: The AI Visibility Platform

## Executive Summary

Market2Agent is the canonical identity and visibility layer for businesses in the AI era.

As AI systems (ChatGPT, Claude, Perplexity, Gemini) become primary discovery channels, businesses face a new challenge: they have no way to know if AI recommends them, no way to compare against competitors, and no way to maintain verified data that AI can trust.

We solve this with three interlocking products:

1. **Entity Registry**: Businesses claim and verify their profiles. We become the source of truth for business entity data, exposed to AI systems via API.

2. **AI Visibility Index**: We continuously monitor what AI systems say about businesses. We provide visibility scores, competitive benchmarks, and alerts.

3. **AI Agent Platform**: Every business gets an AI agent trained on their verified data, deployable on their website and queryable by other systems.

## Market Timing

The transition from search engines to AI assistants is accelerating:
- ChatGPT: 100M+ weekly active users
- Perplexity: Fastest-growing search alternative
- Google SGE/Gemini: Rolling out to billions
- Enterprise AI: Every company deploying internal AI assistants

Businesses spent $80B+ on SEO in 2023. The AI visibility market will be larger because:
- AI recommendations are higher-intent (users act on them immediately)
- There's no "page 2" in AI — you're mentioned or you're not
- Trust signals are different (citations, entity verification, structured data)

## Competitive Landscape

| Player | What They Do | Why We Win |
|--------|--------------|-----------|
| SEMrush, Ahrefs | Traditional SEO | Built for Google, not AI. Wrong architecture. |
| Yext | Local listings management | Focused on directories, not AI systems. |
| Schema.org validators | Check markup syntax | No monitoring, no visibility data, no agents. |
| Brand monitoring tools | Track mentions in press | Don't query AI systems, don't measure AI visibility. |

**Nobody is building the identity + visibility + agent layer for AI. We are.**

## Product Architecture

### Product 1: Entity Registry

**What it is**: A verified database of business entities with canonical structured data.

**User flow**:
1. Business signs up (free tier)
2. Claims their entity by verifying domain ownership
3. Fills out structured profile (name, description, category, locations, leadership, products, social links)
4. We validate and enrich with public data (Wikidata, Wikipedia, SEC, news)
5. Entity gets a public profile page and API endpoint
6. AI systems can query our registry for verified entity data

**Why it matters**:
- AI systems need trusted entity data
- Currently they scrape and hallucinate
- We become the "SSL certificate" for business identity
- Network effect: more entities = more valuable registry

**Pricing**: Free (loss leader for data acquisition)

### Product 2: AI Visibility Index

**What it is**: Continuous monitoring of what AI systems say about businesses.

**How it works**:
1. We maintain a prompt library for every business category
   - "What's the best [category] in [location]?"
   - "Compare [business] vs [competitor]"
   - "What do people say about [business]?"
   - "Who should I hire for [service]?"
2. We query ChatGPT, Claude, Perplexity, Gemini, Copilot daily
3. We parse responses for entity mentions, sentiment, positioning
4. We calculate a Visibility Score (0-100)
5. We track changes over time and vs competitors

**Metrics we provide**:
- **Visibility Score**: How often you appear in AI responses for your category
- **Share of Voice**: Your mentions vs competitors
- **Sentiment Score**: How positively AI describes you
- **Citation Rate**: How often you're cited with links
- **Trend**: How your visibility is changing

**Why it matters**:
- This data doesn't exist anywhere else
- Historical data is irreplaceable (can't go back in time)
- Businesses will pay for competitive intelligence
- Creates urgency ("your competitor is winning")

**Pricing**: $50/mo (Pro), $200/mo (Business with more competitors/prompts)

### Product 3: AI Agent Platform

**What it is**: Every business gets an AI agent trained on their verified data.

**Capabilities**:
- Answers questions about the business accurately
- Trained on registry data + business-provided knowledge base
- Embeddable chat widget for websites
- API endpoint for integrations
- Can respond to queries from other AI systems

**Why it matters**:
- Businesses want to control their AI narrative
- Current chatbots are generic; ours are entity-aware
- Creates stickiness (switching costs)
- Upsell path from registry + visibility

**Pricing**: Included in Business tier, usage-based for Enterprise

### Product 4: Entity API (Enterprise)

**What it is**: API access to our verified entity database.

**Use cases**:
- AI companies need verified business data for grounding
- CRMs need entity enrichment
- Ad platforms need business verification
- Search engines need structured data

**Why it matters**:
- This is the Clearbit/ZoomInfo model
- High-margin, high-volume
- Creates ecosystem dependency

**Pricing**: Usage-based, starting $1K/mo

## Technical Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                                                                 │
│                              MARKET2AGENT PLATFORM                              │
│                                                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐           │
│  │   ENTITY    │  │  VISIBILITY │  │    AGENT    │  │    API      │           │
│  │  REGISTRY   │  │    INDEX    │  │  PLATFORM   │  │  GATEWAY    │           │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘           │
│         │                │                │                │                   │
│         └────────────────┴────────────────┴────────────────┘                   │
│                                    │                                           │
│                                    ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────────────┐  │
│  │                         CORE DATA LAYER                                  │  │
│  │                                                                          │  │
│  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐                │  │
│  │  │    Neo4j      │  │    Redis      │  │   Postgres    │                │  │
│  │  │  Entity Graph │  │  Cache/Queue  │  │  Time Series  │                │  │
│  │  │  Relationships│  │  Rate Limits  │  │  Visibility   │                │  │
│  │  └───────────────┘  └───────────────┘  └───────────────┘                │  │
│  │                                                                          │  │
│  └─────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           EXTERNAL INTEGRATIONS                                  │
│                                                                                  │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐       │
│  │ ChatGPT │ │ Claude  │ │Perplexi-│ │ Gemini  │ │ Copilot │ │ Wikidata│       │
│  │   API   │ │   API   │ │   ty    │ │   API   │ │   API   │ │   API   │       │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘       │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Data Model

### Entity (Neo4j)

```
(:Entity {
    entity_id: uuid,
    slug: string,              // "stripe", "acme-hvac-denver"
    canonical_name: string,
    legal_name: string,
    description: string,
    category: string,          // "payment-processing", "hvac-contractor"
    subcategories: [string],
    
    // Verification
    verified: boolean,
    verification_method: string,
    verified_at: datetime,
    owner_user_id: uuid,
    
    // Structured data
    founded_year: int,
    headquarters: string,
    employee_count_range: string,
    revenue_range: string,
    
    // Web presence
    website: string,
    domains: [string],
    social_links: {twitter, linkedin, facebook, ...},
    
    // Knowledge graph
    wikidata_qid: string,
    wikipedia_url: string,
    crunchbase_url: string,
    
    // AI visibility (denormalized for speed)
    visibility_score: float,
    visibility_updated_at: datetime,
    
    // Metadata
    created_at: datetime,
    updated_at: datetime
})

// Relationships
(:Entity)-[:LOCATED_IN]->(:Location)
(:Entity)-[:IN_CATEGORY]->(:Category)
(:Entity)-[:HAS_PRODUCT]->(:Product)
(:Entity)-[:COMPETES_WITH]->(:Entity)
(:Entity)-[:PARENT_OF]->(:Entity)
(:User)-[:OWNS]->(:Entity)
(:User)-[:TRACKS]->(:Entity)       // Competitor tracking
```

### Visibility Record (Postgres - time series)

```sql
CREATE TABLE visibility_records (
    id BIGSERIAL PRIMARY KEY,
    entity_id UUID NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    
    -- Scores
    visibility_score FLOAT,
    sentiment_score FLOAT,
    citation_rate FLOAT,
    
    -- Breakdown by AI system
    chatgpt_mentions INT,
    claude_mentions INT,
    perplexity_mentions INT,
    gemini_mentions INT,
    
    -- Breakdown by prompt category
    recommendation_mentions INT,
    comparison_mentions INT,
    information_mentions INT,
    
    -- Raw data
    prompt_results JSONB,
    
    INDEX idx_entity_date (entity_id, recorded_at)
);

-- Materialized view for dashboards
CREATE MATERIALIZED VIEW visibility_daily AS
SELECT 
    entity_id,
    DATE(recorded_at) as date,
    AVG(visibility_score) as avg_score,
    SUM(chatgpt_mentions + claude_mentions + perplexity_mentions + gemini_mentions) as total_mentions
FROM visibility_records
GROUP BY entity_id, DATE(recorded_at);
```

## Revenue Model

| Tier | Price | Entities | Competitors | Visibility | Agent | API |
|------|-------|----------|-------------|------------|-------|-----|
| Free | $0 | 1 | 0 | Audit only | No | No |
| Pro | $50/mo | 3 | 3 | Daily monitoring | No | No |
| Business | $200/mo | 10 | 10 | Daily + alerts | Yes | Limited |
| Enterprise | Custom | Unlimited | Unlimited | Real-time | Yes | Full |

**Revenue projection (conservative)**:
- Year 1: 1,000 paying customers, $1.2M ARR
- Year 2: 10,000 paying customers, $12M ARR
- Year 3: 50,000 paying customers + Enterprise API, $60M ARR

## Go-to-Market

### Phase 1: Registry Launch (Month 1-2)
- Launch free entity claiming
- SEO play: public entity pages rank for "[business name] reviews"
- PR: "The first business registry built for AI"
- Target: 10,000 claimed entities

### Phase 2: Visibility Index Beta (Month 3-4)
- Launch visibility monitoring for claimed entities
- Free tier gets monthly snapshot
- Paid tier gets daily monitoring + competitors
- Target: 1,000 paid conversions

### Phase 3: Agent Platform (Month 5-6)
- Launch embeddable AI agents
- Differentiated by entity-awareness
- Target: 500 Business tier upgrades

### Phase 4: Enterprise API (Month 7+)
- Sell entity data to AI companies
- Sell verification badges to platforms
- Target: 3-5 enterprise deals

## Competitive Moats (How We Stay Ahead)

1. **Data Moat**: We accumulate the largest verified business entity graph. This takes years to replicate.

2. **Historical Data**: Our visibility index captures data that can't be recreated. Yesterday's AI responses are gone forever.

3. **Network Effects**: More entities → more valuable registry → AI systems integrate → more entities claim profiles.

4. **Integration Lock-in**: Once AI systems query our registry, switching costs are high.

5. **Trust/Verification**: We become the "certificate authority" for business identity. Hard to displace trusted infrastructure.

## Team Requirements

**Immediate (can outsource)**:
- 1 full-stack engineer (you + contractor)
- 1 content/SEO person (entity pages, blog)

**At $1M ARR**:
- 2 engineers (visibility pipeline, agent platform)
- 1 sales (enterprise)
- 1 customer success

**At $10M ARR**:
- 6 engineers
- 3 sales
- 2 customer success
- 1 product
- 1 marketing

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| AI companies build this themselves | They're focused on models, not vertical SaaS. Partner, don't compete. |
| Google/Microsoft acquires space | Be acquisition target. Build value fast. |
| AI systems don't need external entity data | They already hallucinate. Verified data is valuable. |
| Businesses don't understand AI visibility | Education marketing. Show competitor data. |
| Rate limits on AI APIs | Distributed querying, caching, partnerships. |

## Why Now, Why Us

**Why now**:
- AI search is nascent but inflecting
- First-mover advantage on data accumulation
- Businesses are panicking about AI visibility
- No established player in this space

**Why us**:
- Already built the structured data infrastructure
- Already have paying customers
- Already understand the technical landscape
- Hungry and moving fast

## The Ask

This document is the north star. The next step is execution:
1. Build the Entity Registry (foundation)
2. Build the Visibility Index (revenue engine)
3. Build the Agent Platform (differentiation)
4. Sell to enterprises (scale)

First: claim 10,000 entities. Everything else follows.
