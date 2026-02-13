"""
Market2Agent Platform - AI Visibility Monitoring

This is the core revenue engine. We continuously query AI systems
to track how they perceive, describe, and recommend businesses.

Architecture:
    1. Prompt Library: Category-specific prompts that generate AI responses
    2. Query Engine: Queries ChatGPT, Claude, Perplexity, Gemini
    3. Response Parser: Extracts entity mentions, sentiment, positioning
    4. Scoring Engine: Calculates visibility score (0-100)
    5. Storage: Records snapshots for historical tracking

The data we collect is irreplaceable. Nobody else has it.
"""
import json
import re
import asyncio
import hashlib
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from enum import Enum

import httpx
import structlog

from app.config import settings

logger = structlog.get_logger()


class AISystem(str, Enum):
    CHATGPT = "chatgpt"
    CLAUDE = "claude"
    PERPLEXITY = "perplexity"
    GEMINI = "gemini"


class PromptCategory(str, Enum):
    RECOMMENDATION = "recommendation"  # "What's the best X?"
    COMPARISON = "comparison"          # "X vs Y"
    INFORMATION = "information"        # "Tell me about X"
    REVIEW = "review"                  # "What do people say about X?"
    LOCAL = "local"                    # "Best X in [location]"


# =============================================
# PROMPT LIBRARY
# =============================================

# These prompts are how we query AI systems.
# They're designed to elicit natural commercial responses.

PROMPT_TEMPLATES = {
    PromptCategory.RECOMMENDATION: [
        "What's the best {category} for {use_case}?",
        "What {category} would you recommend for a {audience}?",
        "Top {category} options in 2024?",
        "Which {category} should I use for {use_case}?",
    ],
    PromptCategory.COMPARISON: [
        "Compare {entity} vs {competitor}",
        "{entity} or {competitor} - which is better for {use_case}?",
        "What's the difference between {entity} and {competitor}?",
        "Pros and cons of {entity} compared to {competitor}",
    ],
    PromptCategory.INFORMATION: [
        "Tell me about {entity}",
        "What does {entity} do?",
        "Is {entity} any good?",
        "What is {entity} known for?",
    ],
    PromptCategory.REVIEW: [
        "What do people say about {entity}?",
        "Is {entity} worth it?",
        "{entity} reviews - is it good?",
        "What's the reputation of {entity}?",
    ],
    PromptCategory.LOCAL: [
        "Best {category} in {location}",
        "Recommend a {category} near {location}",
        "Top rated {category} in {location}",
        "Who's the best {category} in {location}?",
    ],
}


# Category-specific use cases and audiences for prompts
CATEGORY_CONTEXTS = {
    "software": {
        "use_cases": ["small business", "enterprise", "startup", "personal use"],
        "audiences": ["developer", "non-technical user", "small business owner", "enterprise team"],
    },
    "saas": {
        "use_cases": ["team collaboration", "project management", "sales", "marketing"],
        "audiences": ["startup founder", "enterprise", "small team", "freelancer"],
    },
    "payment-processing": {
        "use_cases": ["online store", "subscription business", "marketplace", "mobile app"],
        "audiences": ["e-commerce business", "SaaS company", "marketplace", "small business"],
    },
    "crm": {
        "use_cases": ["sales team", "small business", "enterprise", "startup"],
        "audiences": ["sales team", "small business owner", "enterprise", "solopreneur"],
    },
    "hvac": {
        "use_cases": ["home repair", "new installation", "commercial", "maintenance"],
        "audiences": ["homeowner", "business owner", "property manager", "landlord"],
    },
    "restaurant": {
        "use_cases": ["date night", "family dinner", "business lunch", "casual meal"],
        "audiences": ["couple", "family", "business professional", "tourist"],
    },
    # Add more as needed
}


def generate_prompts_for_entity(
    entity_name: str,
    category: str,
    location: Optional[str] = None,
    competitors: List[str] = None,
    max_prompts: int = 20,
) -> List[Dict[str, str]]:
    """
    Generate a set of prompts to query AI systems about an entity.
    Returns list of {prompt, category, variables} dicts.
    """
    prompts = []
    contexts = CATEGORY_CONTEXTS.get(category, {
        "use_cases": ["general use"],
        "audiences": ["business"],
    })
    
    # Recommendation prompts
    for template in PROMPT_TEMPLATES[PromptCategory.RECOMMENDATION][:2]:
        for use_case in contexts["use_cases"][:2]:
            prompts.append({
                "prompt": template.format(category=category, use_case=use_case, audience=contexts["audiences"][0]),
                "category": PromptCategory.RECOMMENDATION,
                "variables": {"category": category, "use_case": use_case},
            })
    
    # Information prompts (always include)
    for template in PROMPT_TEMPLATES[PromptCategory.INFORMATION][:2]:
        prompts.append({
            "prompt": template.format(entity=entity_name),
            "category": PromptCategory.INFORMATION,
            "variables": {"entity": entity_name},
        })
    
    # Review prompts
    for template in PROMPT_TEMPLATES[PromptCategory.REVIEW][:1]:
        prompts.append({
            "prompt": template.format(entity=entity_name),
            "category": PromptCategory.REVIEW,
            "variables": {"entity": entity_name},
        })
    
    # Comparison prompts (if competitors provided)
    if competitors:
        for competitor in competitors[:3]:
            template = PROMPT_TEMPLATES[PromptCategory.COMPARISON][0]
            prompts.append({
                "prompt": template.format(entity=entity_name, competitor=competitor, use_case=contexts["use_cases"][0]),
                "category": PromptCategory.COMPARISON,
                "variables": {"entity": entity_name, "competitor": competitor},
            })
    
    # Local prompts (if location provided)
    if location:
        for template in PROMPT_TEMPLATES[PromptCategory.LOCAL][:2]:
            prompts.append({
                "prompt": template.format(category=category, location=location),
                "category": PromptCategory.LOCAL,
                "variables": {"category": category, "location": location},
            })
    
    return prompts[:max_prompts]


# =============================================
# AI SYSTEM CLIENTS
# =============================================

class AIClient:
    """Base class for AI system clients."""
    
    async def query(self, prompt: str) -> Dict[str, Any]:
        raise NotImplementedError


class ChatGPTClient(AIClient):
    """OpenAI ChatGPT client."""
    
    def __init__(self):
        self.api_key = settings.OPENAI_API_KEY
        self.model = "gpt-4o-mini"  # Cheap and good enough for monitoring
        self.base_url = "https://api.openai.com/v1/chat/completions"
    
    async def query(self, prompt: str) -> Dict[str, Any]:
        if not self.api_key:
            return {"error": "OpenAI API key not configured", "system": "chatgpt"}
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    self.base_url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 1000,
                        "temperature": 0.7,
                    },
                    timeout=30,
                )
                response.raise_for_status()
                data = response.json()
                
                return {
                    "system": "chatgpt",
                    "model": self.model,
                    "response": data["choices"][0]["message"]["content"],
                    "prompt_tokens": data["usage"]["prompt_tokens"],
                    "completion_tokens": data["usage"]["completion_tokens"],
                }
            except Exception as e:
                logger.error("chatgpt_query_failed", error=str(e))
                return {"error": str(e), "system": "chatgpt"}


class ClaudeClient(AIClient):
    """Anthropic Claude client."""
    
    def __init__(self):
        self.api_key = settings.ANTHROPIC_API_KEY
        self.model = "claude-3-haiku-20240307"  # Cheapest, fast
        self.base_url = "https://api.anthropic.com/v1/messages"
    
    async def query(self, prompt: str) -> Dict[str, Any]:
        if not self.api_key:
            return {"error": "Anthropic API key not configured", "system": "claude"}
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    self.base_url,
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 1000,
                    },
                    timeout=30,
                )
                response.raise_for_status()
                data = response.json()
                
                return {
                    "system": "claude",
                    "model": self.model,
                    "response": data["content"][0]["text"],
                    "prompt_tokens": data["usage"]["input_tokens"],
                    "completion_tokens": data["usage"]["output_tokens"],
                }
            except Exception as e:
                logger.error("claude_query_failed", error=str(e))
                return {"error": str(e), "system": "claude"}


class PerplexityClient(AIClient):
    """Perplexity API client."""
    
    def __init__(self):
        self.api_key = settings.PERPLEXITY_API_KEY
        self.model = "llama-3.1-sonar-small-128k-online"
        self.base_url = "https://api.perplexity.ai/chat/completions"
    
    async def query(self, prompt: str) -> Dict[str, Any]:
        if not self.api_key:
            return {"error": "Perplexity API key not configured", "system": "perplexity"}
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    self.base_url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 1000,
                    },
                    timeout=30,
                )
                response.raise_for_status()
                data = response.json()
                
                return {
                    "system": "perplexity",
                    "model": self.model,
                    "response": data["choices"][0]["message"]["content"],
                    "citations": data.get("citations", []),
                }
            except Exception as e:
                logger.error("perplexity_query_failed", error=str(e))
                return {"error": str(e), "system": "perplexity"}


class GeminiClient(AIClient):
    """Google Gemini client."""
    
    def __init__(self):
        self.api_key = settings.GOOGLE_AI_API_KEY
        self.model = "gemini-1.5-flash"
        self.base_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
    
    async def query(self, prompt: str) -> Dict[str, Any]:
        if not self.api_key:
            return {"error": "Google AI API key not configured", "system": "gemini"}
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}?key={self.api_key}",
                    headers={"Content-Type": "application/json"},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"maxOutputTokens": 1000},
                    },
                    timeout=30,
                )
                response.raise_for_status()
                data = response.json()
                
                return {
                    "system": "gemini",
                    "model": self.model,
                    "response": data["candidates"][0]["content"]["parts"][0]["text"],
                }
            except Exception as e:
                logger.error("gemini_query_failed", error=str(e))
                return {"error": str(e), "system": "gemini"}


# Client registry
AI_CLIENTS = {
    AISystem.CHATGPT: ChatGPTClient(),
    AISystem.CLAUDE: ClaudeClient(),
    AISystem.PERPLEXITY: PerplexityClient(),
    AISystem.GEMINI: GeminiClient(),
}


# =============================================
# RESPONSE PARSER
# =============================================

@dataclass
class MentionResult:
    """Result of checking if an entity is mentioned in a response."""
    mentioned: bool
    sentiment: str  # "positive", "negative", "neutral"
    position: Optional[int]  # Position in list (1 = first mentioned)
    is_recommended: bool  # Explicitly recommended
    context: str  # Surrounding text
    confidence: float  # 0-1


def parse_response_for_entity(
    response_text: str,
    entity_name: str,
    competitor_names: List[str] = None,
) -> MentionResult:
    """
    Parse an AI response to determine if/how an entity is mentioned.
    """
    text_lower = response_text.lower()
    entity_lower = entity_name.lower()
    
    # Check for mention
    mentioned = entity_lower in text_lower
    
    if not mentioned:
        # Try variations (e.g., "Stripe" might appear as "Stripe, Inc.")
        mentioned = any(
            variant in text_lower 
            for variant in [
                entity_lower.replace(" ", ""),
                entity_lower.replace("-", " "),
                entity_lower.split()[0] if " " in entity_lower else entity_lower,
            ]
        )
    
    if not mentioned:
        return MentionResult(
            mentioned=False,
            sentiment="neutral",
            position=None,
            is_recommended=False,
            context="",
            confidence=0.9,
        )
    
    # Find position (if competitors mentioned)
    position = 1
    if competitor_names:
        all_entities = [entity_name] + competitor_names
        positions = []
        for ent in all_entities:
            idx = text_lower.find(ent.lower())
            if idx >= 0:
                positions.append((ent, idx))
        positions.sort(key=lambda x: x[1])
        for i, (ent, _) in enumerate(positions):
            if ent.lower() == entity_lower:
                position = i + 1
                break
    
    # Analyze sentiment (simple keyword approach)
    positive_signals = [
        "recommend", "best", "top", "excellent", "great", "leading",
        "popular", "trusted", "reliable", "good choice", "solid",
        "well-regarded", "highly rated", "strong", "preferred",
    ]
    negative_signals = [
        "avoid", "not recommended", "issues", "problems", "concerns",
        "complaints", "expensive", "difficult", "limited", "lacking",
        "drawback", "downside", "worse", "behind",
    ]
    
    # Check for signals near the entity mention
    entity_idx = text_lower.find(entity_lower)
    context_start = max(0, entity_idx - 200)
    context_end = min(len(text_lower), entity_idx + 200)
    context = response_text[context_start:context_end]
    context_lower = context.lower()
    
    positive_count = sum(1 for s in positive_signals if s in context_lower)
    negative_count = sum(1 for s in negative_signals if s in context_lower)
    
    if positive_count > negative_count:
        sentiment = "positive"
    elif negative_count > positive_count:
        sentiment = "negative"
    else:
        sentiment = "neutral"
    
    # Check if explicitly recommended
    is_recommended = any(
        phrase in context_lower
        for phrase in [
            f"recommend {entity_lower}",
            f"{entity_lower} is a great",
            f"{entity_lower} is the best",
            f"go with {entity_lower}",
            f"try {entity_lower}",
            f"suggest {entity_lower}",
        ]
    )
    
    return MentionResult(
        mentioned=True,
        sentiment=sentiment,
        position=position,
        is_recommended=is_recommended,
        context=context.strip(),
        confidence=0.85,
    )


# =============================================
# VISIBILITY SCORING
# =============================================

@dataclass
class VisibilityScore:
    """Calculated visibility score for an entity."""
    overall_score: float  # 0-100
    
    # Breakdown
    mention_rate: float      # % of prompts where mentioned
    sentiment_score: float   # -1 to 1, normalized to 0-100
    position_score: float    # Based on where in response
    recommendation_rate: float  # % of times explicitly recommended
    
    # Per-system breakdown
    system_scores: Dict[str, float]
    
    # Per-category breakdown
    category_scores: Dict[str, float]
    
    # Trend (vs last measurement)
    trend: str  # "up", "down", "stable", "new"
    trend_delta: float


def calculate_visibility_score(
    results: List[Dict[str, Any]],
    previous_score: Optional[float] = None,
) -> VisibilityScore:
    """
    Calculate visibility score from query results.
    
    Score components:
    - Mention rate (40%): How often the entity appears in responses
    - Sentiment (20%): How positively described
    - Position (20%): How early/prominently mentioned
    - Recommendation rate (20%): How often explicitly recommended
    """
    if not results:
        return VisibilityScore(
            overall_score=0,
            mention_rate=0,
            sentiment_score=50,
            position_score=0,
            recommendation_rate=0,
            system_scores={},
            category_scores={},
            trend="new",
            trend_delta=0,
        )
    
    total_queries = len(results)
    mentions = [r for r in results if r.get("mentioned")]
    
    # Mention rate
    mention_rate = len(mentions) / total_queries if total_queries > 0 else 0
    
    # Sentiment (average, convert -1 to 1 scale to 0-100)
    sentiment_values = {"positive": 1, "neutral": 0, "negative": -1}
    sentiments = [sentiment_values.get(r.get("sentiment", "neutral"), 0) for r in mentions]
    avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0
    sentiment_score = (avg_sentiment + 1) * 50  # Convert to 0-100
    
    # Position score (earlier = better, max 5 positions)
    positions = [r.get("position", 5) for r in mentions if r.get("position")]
    avg_position = sum(positions) / len(positions) if positions else 5
    position_score = max(0, (6 - avg_position) / 5) * 100  # 1st = 100, 5th+ = 0-20
    
    # Recommendation rate
    recommendations = sum(1 for r in mentions if r.get("is_recommended"))
    recommendation_rate = recommendations / total_queries if total_queries > 0 else 0
    
    # Calculate overall score
    overall_score = (
        mention_rate * 40 +
        (sentiment_score / 100) * 20 +
        (position_score / 100) * 20 +
        recommendation_rate * 20
    )
    
    # Per-system breakdown
    system_scores = {}
    for system in AISystem:
        system_results = [r for r in results if r.get("system") == system.value]
        if system_results:
            system_mentions = sum(1 for r in system_results if r.get("mentioned"))
            system_scores[system.value] = (system_mentions / len(system_results)) * 100
    
    # Per-category breakdown
    category_scores = {}
    for cat in PromptCategory:
        cat_results = [r for r in results if r.get("prompt_category") == cat.value]
        if cat_results:
            cat_mentions = sum(1 for r in cat_results if r.get("mentioned"))
            category_scores[cat.value] = (cat_mentions / len(cat_results)) * 100
    
    # Trend
    if previous_score is None:
        trend = "new"
        trend_delta = 0
    else:
        trend_delta = overall_score - previous_score
        if trend_delta > 2:
            trend = "up"
        elif trend_delta < -2:
            trend = "down"
        else:
            trend = "stable"
    
    return VisibilityScore(
        overall_score=round(overall_score, 1),
        mention_rate=round(mention_rate * 100, 1),
        sentiment_score=round(sentiment_score, 1),
        position_score=round(position_score, 1),
        recommendation_rate=round(recommendation_rate * 100, 1),
        system_scores=system_scores,
        category_scores=category_scores,
        trend=trend,
        trend_delta=round(trend_delta, 1),
    )


# =============================================
# VISIBILITY INDEXER
# =============================================

async def run_visibility_check(
    entity_name: str,
    category: str,
    location: Optional[str] = None,
    competitors: List[str] = None,
    systems: List[AISystem] = None,
) -> Dict[str, Any]:
    """
    Run a full visibility check for an entity.
    Queries all AI systems with generated prompts.
    Returns raw results for storage and scoring.
    """
    if systems is None:
        systems = list(AISystem)
    
    # Generate prompts
    prompts = generate_prompts_for_entity(
        entity_name=entity_name,
        category=category,
        location=location,
        competitors=competitors,
    )
    
    results = []
    
    # Query each system with each prompt
    for prompt_info in prompts:
        prompt = prompt_info["prompt"]
        prompt_category = prompt_info["category"]
        
        for system in systems:
            client = AI_CLIENTS.get(system)
            if not client:
                continue
            
            # Query AI system
            response = await client.query(prompt)
            
            if "error" in response:
                logger.warning("visibility_query_error",
                             system=system.value,
                             error=response["error"])
                continue
            
            # Parse response for entity mention
            mention_result = parse_response_for_entity(
                response_text=response.get("response", ""),
                entity_name=entity_name,
                competitor_names=competitors,
            )
            
            results.append({
                "prompt": prompt,
                "prompt_category": prompt_category.value,
                "system": system.value,
                "response": response.get("response", ""),
                "mentioned": mention_result.mentioned,
                "sentiment": mention_result.sentiment,
                "position": mention_result.position,
                "is_recommended": mention_result.is_recommended,
                "context": mention_result.context,
                "queried_at": datetime.now(timezone.utc).isoformat(),
            })
            
            # Rate limiting between queries
            await asyncio.sleep(0.5)
    
    return {
        "entity_name": entity_name,
        "category": category,
        "location": location,
        "competitors": competitors,
        "results": results,
        "total_queries": len(results),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def index_entity_visibility(
    entity_id: str,
    entity_name: str,
    category: str,
    location: Optional[str] = None,
    competitors: List[str] = None,
) -> VisibilityScore:
    """
    Full visibility indexing workflow for an entity.
    1. Run visibility check
    2. Calculate score
    3. Store results
    4. Update entity
    """
    from app.entities.model import get_entity_by_id, update_visibility_score
    
    # Get previous score for trend calculation
    entity = get_entity_by_id(entity_id)
    previous_score = entity.visibility_score if entity else None
    
    # Run visibility check
    check_results = await run_visibility_check(
        entity_name=entity_name,
        category=category,
        location=location,
        competitors=competitors,
    )
    
    # Calculate score
    score = calculate_visibility_score(
        results=check_results["results"],
        previous_score=previous_score,
    )
    
    # Store record (in production, this goes to Postgres time-series)
    # For now, log it
    logger.info("visibility_indexed",
                entity_id=entity_id,
                entity_name=entity_name,
                score=score.overall_score,
                trend=score.trend,
                mention_rate=score.mention_rate)
    
    # Update entity's denormalized visibility score
    update_visibility_score(entity_id, score.overall_score, score.trend)
    
    return score
