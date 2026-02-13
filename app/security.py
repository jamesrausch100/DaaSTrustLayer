"""
Market2Agent — Security Layer
Re-exports auth dependencies for API modules.
"""
from app.auth import get_current_user, require_auth, require_subscription
from app.config import settings
from fastapi import Request, HTTPException


async def require_admin(request: Request) -> dict:
    """Require admin access — checks user email against admin list."""
    user = await require_auth(request)
    if user.get("email", "").lower() not in settings.ADMIN_EMAILS:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
