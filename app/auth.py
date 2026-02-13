"""
Market2Agent - Authentication
Google OAuth implementation with session management.
"""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
import httpx
from fastapi import APIRouter, HTTPException, Request, Response, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
import jwt

from app.config import settings
from app.db import get_session

router = APIRouter(prefix="/v1/auth", tags=["auth"])

# JWT settings
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 30


# ===========================================
# Models
# ===========================================

class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    subscription_status: str
    subscription_tier: str


class TokenData(BaseModel):
    user_id: str
    email: str


# ===========================================
# JWT Token Helpers
# ===========================================

def create_access_token(user_id: str, email: str) -> str:
    """Create a JWT access token."""
    payload = {
        "user_id": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRY_DAYS),
        "iat": datetime.now(timezone.utc)
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[TokenData]:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return TokenData(user_id=payload["user_id"], email=payload["email"])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ===========================================
# Auth Dependency
# ===========================================

async def get_current_user(request: Request) -> Optional[dict]:
    """Get current user from JWT cookie or Authorization header."""
    token = None
    
    # Check cookie first
    token = request.cookies.get("access_token")
    
    # Check Authorization header
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]
    
    if not token:
        return None
    
    token_data = decode_access_token(token)
    if not token_data:
        return None
    
    # Get user from database
    
    with get_session() as session:
        result = session.run("""
            MATCH (u:User {id: $user_id})
            RETURN u
        """, user_id=token_data.user_id)
        
        record = result.single()
        if not record:
            return None
        
        return dict(record["u"])


async def require_auth(request: Request) -> dict:
    """Require authentication - raises 401 if not logged in."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


async def require_subscription(request: Request) -> dict:
    """Require active subscription - raises 403 if not subscribed."""
    user = await require_auth(request)
    if user.get("subscription_status") != "active":
        raise HTTPException(status_code=403, detail="Active subscription required")
    return user


# ===========================================
# Database Operations
# ===========================================

def create_or_update_user(google_id: str, email: str, name: str) -> dict:
    """Create a new user or update existing one from Google OAuth."""
    
    
    with get_session() as session:
        result = session.run("""
            MERGE (u:User {google_id: $google_id})
            ON CREATE SET
                u.id = randomUUID(),
                u.email = $email,
                u.name = $name,
                u.created_at = datetime(),
                u.subscription_status = 'free',
                u.subscription_tier = 'free',
                u.stripe_customer_id = null
            ON MATCH SET
                u.email = $email,
                u.name = $name,
                u.last_login = datetime()
            RETURN u
        """, google_id=google_id, email=email, name=name)
        
        record = result.single()
        return dict(record["u"])


def get_user_by_id(user_id: str) -> Optional[dict]:
    """Get user by ID."""
    
    
    with get_session() as session:
        result = session.run("""
            MATCH (u:User {id: $user_id})
            RETURN u
        """, user_id=user_id)
        
        record = result.single()
        return dict(record["u"]) if record else None


# ===========================================
# Google OAuth Endpoints
# ===========================================

@router.get("/google")
async def google_login(response: Response):
    """Redirect to Google OAuth consent screen."""
    state = secrets.token_urlsafe(32)
    
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": f"{settings.API_URL}/v1/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",
        "prompt": "consent"
    }
    
    query = "&".join(f"{k}={v}" for k, v in params.items())
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{query}"
    
    redirect = RedirectResponse(url=auth_url)
    redirect.set_cookie(
        key="oauth_state",
        value=state,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        max_age=600,  # 10 minutes
    )
    return redirect


@router.get("/google/callback")
async def google_callback(request: Request, code: str, state: str, response: Response):
    """Handle Google OAuth callback."""
    
    # SEC-08: Validate state parameter against cookie
    stored_state = request.cookies.get("oauth_state")
    if not stored_state or stored_state != state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state. Possible CSRF attack.")
    
    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": f"{settings.API_URL}/v1/auth/google/callback"
            }
        )
        
        if token_response.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to exchange code for token")
        
        tokens = token_response.json()
        access_token = tokens["access_token"]
        
        # Get user info from Google
        userinfo_response = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        
        if userinfo_response.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to get user info")
        
        userinfo = userinfo_response.json()
    
    # Create or update user in database
    user = create_or_update_user(
        google_id=userinfo["id"],
        email=userinfo["email"],
        name=userinfo.get("name", userinfo["email"])
    )
    
    # Create JWT token
    jwt_token = create_access_token(user["id"], user["email"])
    
    # BE-03: Redirect to SPA root (not dashboard.html which doesn't exist)
    redirect_response = RedirectResponse(url=f"{settings.APP_URL}/?auth=success")
    redirect_response.set_cookie(
        key="access_token",
        value=jwt_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=60 * 60 * 24 * JWT_EXPIRY_DAYS  # 30 days
    )
    
    return redirect_response


@router.get("/me", response_model=UserResponse)
async def get_me(user: dict = Depends(require_auth)):
    """Get current authenticated user."""
    return UserResponse(
        id=user["id"],
        email=user["email"],
        name=user["name"],
        subscription_status=user.get("subscription_status", "free"),
        subscription_tier=user.get("subscription_tier", "free")
    )


@router.post("/logout")
async def logout(response: Response):
    """Logout - clear the access token cookie."""
    response = RedirectResponse(url=settings.APP_URL)
    response.delete_cookie("access_token")
    return response
