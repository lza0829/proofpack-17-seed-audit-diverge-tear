from __future__ import annotations

import sqlite3
from typing import Any


def clear_for_pack(conn: sqlite3.Connection, pack_id: int) -> None:
    conn.execute("DELETE FROM checklist_results WHERE pack_id=?", (pack_id,))


def insert_result(
    conn: sqlite3.Connection,
    *,
    pack_id: int,
    check_code: str,
    check_title: str,
    passed: bool,
    detail: str,
    created_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO checklist_results(
            pack_id, check_code, check_title, passed, detail, created_at
        ) VALUES (?,?,?,?,?,?)
        """,
        (pack_id, check_code, check_title, 1 if passed else 0, detail, created_at),
    )
    return int(cur.lastrowid)


def list_results(
    conn: sqlite3.Connection,
    pack_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if pack_id is not None:
        rows = conn.execute(
            """
            SELECT id, pack_id, check_code, check_title, passed, detail, created_at
            FROM checklist_results
            WHERE pack_id=?
            ORDER BY id
            """,
            (pack_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, pack_id, check_code, check_title, passed, detail, created_at
            FROM checklist_results
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def all_passed(conn: sqlite3.Connection, pack_id: int) -> bool:
    rows = conn.execute(
        "SELECT passed FROM checklist_results WHERE pack_id=?",
        (pack_id,),
    ).fetchall()
    if not rows:
        return False
    return all(int(r["passed"]) == 1 for r in rows)
