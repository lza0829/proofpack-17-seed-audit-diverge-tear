from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.policies import DEFAULT_BATCH_POLICY, DEFAULT_SEED_POLICY, WRITABLE_SETTINGS
from app.repositories import audit as audit_repo
from app.repositories import settings as settings_repo


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def list_settings() -> dict[str, str]:
    conn = connect()
    try:
        return settings_repo.list_settings(conn)
    finally:
        conn.close()


def get_policies(conn) -> tuple[str, str]:
    """Read the two mutually exclusive policies, falling back to safe defaults."""
    seed_policy = (
        settings_repo.get_setting(conn, "seed_policy", DEFAULT_SEED_POLICY)
        or DEFAULT_SEED_POLICY
    )
    batch_policy = (
        settings_repo.get_setting(conn, "batch_policy", DEFAULT_BATCH_POLICY)
        or DEFAULT_BATCH_POLICY
    )
    return seed_policy, batch_policy


def update_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """切换互斥策略。

    一次请求每个策略键只能落到单一取值；未知键与非法取值都直接拒绝，不做静默兜底。
    """
    unknown = sorted(set(payload) - set(WRITABLE_SETTINGS))
    if unknown:
        raise HTTPException(status_code=400, detail=f"不可改写的配置项: {', '.join(unknown)}")

    updates: dict[str, str] = {}
    for key, raw in payload.items():
        value = str(raw).strip()
        choices = WRITABLE_SETTINGS[key]
        if value not in choices:
            raise HTTPException(
                status_code=400,
                detail=f"{key} 取值须为 {' / '.join(choices)}，收到 {value!r}",
            )
        updates[key] = value

    if not updates:
        raise HTTPException(status_code=400, detail="未提供任何可改写配置项")

    conn = connect()
    try:
        before = settings_repo.list_settings(conn)
        for key, value in updates.items():
            settings_repo.set_setting(conn, key, value)
        audit_repo.create(
            conn,
            event_type="settings_update",
            detail={
                "before": {k: before.get(k) for k in updates},
                "after": updates,
            },
            created_at=_now(),
        )
        conn.commit()
        return {"updated": updates, "settings": settings_repo.list_settings(conn)}
    finally:
        conn.close()
