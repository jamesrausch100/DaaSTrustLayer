"""
Market2Agent — Careers API + Waitlist
Collects intern applications (stores to Neo4j) and API waitlist signups.
"""
import uuid
import base64
from datetime import datetime, timezone
from fastapi import APIRouter, UploadFile, File, Form, Header, HTTPException
from fastapi.responses import JSONResponse
from typing import Optional
import structlog

from app.config import settings

logger = structlog.get_logger()

router = APIRouter(prefix="/v1", tags=["careers"])

MAX_RESUME_BYTES = 5 * 1024 * 1024  # 5 MB


@router.post("/careers/apply")
async def apply(
    name: str = Form(...),
    email: str = Form(...),
    linkedin: Optional[str] = Form(""),
    role: Optional[str] = Form("general"),
    note: Optional[str] = Form(""),
    resume: Optional[UploadFile] = File(None),
):
    """Accept an intern application."""
    app_id = str(uuid.uuid4())[:12]
    now = datetime.now(timezone.utc).isoformat()

    # SEC-04: Enforce file size limit
    resume_data = None
    resume_name = None
    if resume and resume.filename:
        content = await resume.read()
        if len(content) > MAX_RESUME_BYTES:
            raise HTTPException(status_code=413, detail=f"Resume too large. Max {MAX_RESUME_BYTES // (1024*1024)}MB.")
        resume_data = base64.b64encode(content).decode("utf-8")
        resume_name = resume.filename
        logger.info("resume_received", app_id=app_id, filename=resume_name, size_kb=round(len(content) / 1024, 1))

    try:
        from app.db.neo4j import get_session
        with get_session() as session:
            session.run("""
                CREATE (a:Application {
                    app_id: $app_id, name: $name, email: $email,
                    linkedin: $linkedin, role: $role, note: $note,
                    resume_filename: $resume_name, resume_data: $resume_data,
                    applied_at: $now, status: 'new', type: 'application'
                })
            """,
                app_id=app_id, name=name, email=email,
                linkedin=linkedin or "", role=role or "general",
                note=note or "", resume_name=resume_name or "",
                resume_data=resume_data or "", now=now,
            )
        logger.info("application_stored", app_id=app_id, name=name, role=role)
    except Exception as e:
        logger.error("application_storage_failed", app_id=app_id, error=str(e))

    return JSONResponse(status_code=201, content={
        "status": "received", "application_id": app_id,
        "message": "Application received. We review every submission personally.",
    })


@router.post("/waitlist/join")
async def join_waitlist(
    name: str = Form(""),
    email: str = Form(...),
    role: Optional[str] = Form("api-waitlist"),
    note: Optional[str] = Form(""),
):
    """MIS-04: Separate waitlist endpoint — doesn't pollute applications."""
    wl_id = str(uuid.uuid4())[:12]
    now = datetime.now(timezone.utc).isoformat()

    try:
        from app.db.neo4j import get_session
        with get_session() as session:
            session.run("""
                MERGE (w:WaitlistEntry {email: $email})
                ON CREATE SET w.wl_id = $wl_id, w.name = $name,
                    w.role = $role, w.note = $note,
                    w.joined_at = $now, w.status = 'pending'
                ON MATCH SET w.name = CASE WHEN $name <> '' THEN $name ELSE w.name END,
                    w.updated_at = $now
            """,
                wl_id=wl_id, name=name, email=email,
                role=role or "api-waitlist", note=note or "", now=now,
            )
        logger.info("waitlist_joined", wl_id=wl_id, email=email)
    except Exception as e:
        logger.error("waitlist_storage_failed", error=str(e))

    return JSONResponse(status_code=201, content={
        "status": "joined", "message": "You're on the list. We'll send your API key shortly.",
    })


@router.get("/careers/applications")
async def list_applications(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """SEC-02: List applications. Auth via X-Admin-Key header (not query string)."""
    if not x_admin_key or x_admin_key != settings.M2A_ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden. Provide X-Admin-Key header.")

    try:
        from app.db.neo4j import get_session
        with get_session() as session:
            result = session.run("""
                MATCH (a:Application)
                RETURN a.app_id AS id, a.name AS name, a.email AS email,
                       a.linkedin AS linkedin, a.role AS role, a.note AS note,
                       a.resume_filename AS resume, a.applied_at AS applied_at,
                       a.status AS status, a.type AS type
                ORDER BY a.applied_at DESC
            """)
            apps = [dict(record) for record in result]
        return {"count": len(apps), "applications": apps}
    except Exception as e:
        logger.error("application_list_failed", error=str(e))
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/waitlist")
async def list_waitlist(
    x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key"),
):
    """List waitlist entries. Admin only."""
    if not x_admin_key or x_admin_key != settings.M2A_ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden. Provide X-Admin-Key header.")

    try:
        from app.db.neo4j import get_session
        with get_session() as session:
            result = session.run("""
                MATCH (w:WaitlistEntry)
                RETURN w.wl_id AS id, w.name AS name, w.email AS email,
                       w.role AS role, w.joined_at AS joined_at, w.status AS status
                ORDER BY w.joined_at DESC
            """)
            entries = [dict(record) for record in result]
        return {"count": len(entries), "entries": entries}
    except Exception as e:
        logger.error("waitlist_list_failed", error=str(e))
        return JSONResponse(status_code=500, content={"error": str(e)})
