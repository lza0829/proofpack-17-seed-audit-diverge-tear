from __future__ import annotations

import json
import sqlite3
from typing import Any

BATCH_COLUMNS = """
    id, batch_no, subject_id, seed, rate, source_count, sample_count, status, created_at,
    algorithm, seed_source, source_fingerprint, sample_keys_fingerprint
"""


def create_batch(
    conn: sqlite3.Connection,
    *,
    batch_no: str,
    subject_id: int,
    seed: str,
    rate: float,
    source_count: int,
    sample_count: int,
    status: str,
    created_at: str,
    algorithm: str,
    seed_source: str,
    source_fingerprint: str | None,
    sample_keys_fingerprint: str | None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO sample_batches(
            batch_no, subject_id, seed, rate, source_count, sample_count, status, created_at,
            algorithm, seed_source, source_fingerprint, sample_keys_fingerprint
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            batch_no,
            subject_id,
            seed,
            rate,
            source_count,
            sample_count,
            status,
            created_at,
            algorithm,
            seed_source,
            source_fingerprint,
            sample_keys_fingerprint,
        ),
    )
    return int(cur.lastrowid)


def insert_rows(
    conn: sqlite3.Connection,
    batch_id: int,
    rows: list[dict[str, Any]],
) -> None:
    for r in rows:
        conn.execute(
            """
            INSERT INTO sample_rows(batch_id, business_key, payload_json, event_ts, revision)
            VALUES (?,?,?,?,?)
            """,
            (
                batch_id,
                str(r.get("business_key", "")),
                json.dumps(r, ensure_ascii=False),
                r.get("event_ts"),
                r.get("revision"),
            ),
        )


def list_batches(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {BATCH_COLUMNS} FROM sample_batches ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_for_subject(conn: sqlite3.Connection, subject_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {BATCH_COLUMNS} FROM sample_batches WHERE subject_id=? ORDER BY id",
        (subject_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_batch(conn: sqlite3.Connection, batch_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT {BATCH_COLUMNS} FROM sample_batches WHERE id=?",
        (batch_id,),
    ).fetchone()
    return dict(row) if row else None


def latest_ready_for_subject(conn: sqlite3.Connection, subject_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        f"""
        SELECT {BATCH_COLUMNS} FROM sample_batches
        WHERE subject_id=? AND status='ready'
        ORDER BY id DESC LIMIT 1
        """,
        (subject_id,),
    ).fetchone()
    return dict(row) if row else None


def latest_ready_batches(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    """Latest ready batch per subject, keyed by subject_id."""
    rows = conn.execute(
        f"""
        SELECT {BATCH_COLUMNS} FROM sample_batches AS b
        WHERE status='ready'
          AND id = (
              SELECT MAX(id) FROM sample_batches
              WHERE subject_id=b.subject_id AND status='ready'
          )
        """
    ).fetchall()
    return {int(r["subject_id"]): dict(r) for r in rows}


def list_rows(conn: sqlite3.Connection, batch_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, batch_id, business_key, payload_json, event_ts, revision
        FROM sample_rows WHERE batch_id=? ORDER BY business_key
        """,
        (batch_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        payload_json = d.pop("payload_json")
        try:
            d["payload"] = json.loads(payload_json)
        except json.JSONDecodeError:
            d["payload"] = payload_json
        out.append(d)
    return out


def update_status(conn: sqlite3.Connection, batch_id: int, status: str) -> None:
    conn.execute("UPDATE sample_batches SET status=? WHERE id=?", (status, batch_id))


# 注意：本仓储不提供任何改写批次 seed 的接口。
# 批次种子在 create_batch 落库后即冻结——它必须始终是抽样判定实际采用的值，
# 任何“事后改种子”的写法都会让卡片与真实抽样脱节。
