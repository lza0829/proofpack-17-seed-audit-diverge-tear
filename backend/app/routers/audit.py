from fastapi import APIRouter, Query

from app.db import connect
from app.repositories import audit as audit_repo

router = APIRouter(tags=["audit"])


@router.get("/audit")
def list_audit(subject_id: int | None = Query(default=None), limit: int = Query(default=50, ge=1, le=200)):
    conn = connect()
    try:
        return audit_repo.list_events(conn, subject_id=subject_id, limit=limit)
    finally:
        conn.close()
