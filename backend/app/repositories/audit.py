from __future__ import annotations

import json
import sqlite3
from typing import Any


def create(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    detail: dict[str, Any],
    created_at: str,
    subject_id: int | None = None,
    batch_id: int | None = None,
    pack_id: int | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO audit_events(event_type, subject_id, batch_id, pack_id, detail_json, created_at)
        VALUES (?,?,?,?,?,?)
        """,
        (
            event_type,
            subject_id,
            batch_id,
            pack_id,
            json.dumps(detail, ensure_ascii=False, sort_keys=True),
            created_at,
        ),
    )
    return int(cur.lastrowid)


def list_events(
    conn: sqlite3.Connection,
    *,
    subject_id: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if subject_id is None:
        rows = conn.execute(
            """
            SELECT id, event_type, subject_id, batch_id, pack_id, detail_json, created_at
            FROM audit_events ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, event_type, subject_id, batch_id, pack_id, detail_json, created_at
            FROM audit_events WHERE subject_id=? ORDER BY id DESC LIMIT ?
            """,
            (subject_id, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        raw = d.pop("detail_json")
        try:
            d["detail"] = json.loads(raw)
        except json.JSONDecodeError:
            d["detail"] = raw
        out.append(d)
    return out


def clear_all(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM audit_events")
