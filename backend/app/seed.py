from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.db import connect
from app.engines.sample import SAMPLING_ALGORITHM
from app.policies import DEFAULT_BATCH_POLICY, DEFAULT_SEED_POLICY

# 举证对象种子
SUBJECT_SEEDS = [
    {
        "code": "store_dayend",
        "title": "门店日结抽检",
        "domain": "store",
        "fixture": "store_dayend.json",
        "sample_rate_default": 0.3,
        "enabled": 1,
    },
    {
        "code": "channel_settle",
        "title": "渠道结算抽检",
        "domain": "channel",
        "fixture": "channel_settle.json",
        "sample_rate_default": 0.3,
        "enabled": 1,
    },
    {
        "code": "price_change",
        "title": "价目变更核证",
        "domain": "price",
        "fixture": "price_change.json",
        "sample_rate_default": 0.3,
        "enabled": 1,
    },
]

# 设置默认值。seed_policy / batch_policy 各自是一组互斥取值。
DEFAULT_SETTINGS = {
    "default_sample_rate": "0.3",
    "require_checklist_pass": "1",
    # 取值与语义见 app/policies.py
    "seed_policy": DEFAULT_SEED_POLICY,
    "batch_policy": DEFAULT_BATCH_POLICY,
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Lightweight forward migration: add a column if an older DB lacks it."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _migrate(conn: sqlite3.Connection) -> None:
    # 采样批次的可复现审计字段
    _add_column(conn, "sample_batches", "algorithm", f"TEXT NOT NULL DEFAULT '{SAMPLING_ALGORITHM}'")
    _add_column(conn, "sample_batches", "seed_source", "TEXT NOT NULL DEFAULT 'explicit'")
    _add_column(conn, "sample_batches", "source_fingerprint", "TEXT")
    _add_column(conn, "sample_batches", "sample_keys_fingerprint", "TEXT")


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(
            """
            -- 举证对象
            CREATE TABLE IF NOT EXISTS subjects(
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                domain TEXT NOT NULL,
                fixture TEXT NOT NULL,
                sample_rate_default REAL NOT NULL DEFAULT 0.3,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            -- 采样批次
            -- seed / rate / source_count / sample_count 是批次必须写明的举证要素；
            -- algorithm / source_fingerprint / sample_keys_fingerprint 用于事后复核同种子重跑。
            CREATE TABLE IF NOT EXISTS sample_batches(
                id INTEGER PRIMARY KEY,
                batch_no TEXT NOT NULL UNIQUE,
                subject_id INTEGER NOT NULL,
                seed TEXT NOT NULL,
                rate REAL NOT NULL,
                source_count INTEGER NOT NULL,
                sample_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                algorithm TEXT NOT NULL DEFAULT 'sha256-bucket-v1',
                seed_source TEXT NOT NULL DEFAULT 'explicit',
                source_fingerprint TEXT,
                sample_keys_fingerprint TEXT,
                FOREIGN KEY(subject_id) REFERENCES subjects(id)
            );
            -- 样本行
            CREATE TABLE IF NOT EXISTS sample_rows(
                id INTEGER PRIMARY KEY,
                batch_id INTEGER NOT NULL,
                business_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                event_ts TEXT,
                revision INTEGER,
                FOREIGN KEY(batch_id) REFERENCES sample_batches(id)
            );
            -- 证据包
            CREATE TABLE IF NOT EXISTS evidence_packs(
                id INTEGER PRIMARY KEY,
                pack_no TEXT NOT NULL UNIQUE,
                batch_id INTEGER NOT NULL,
                subject_id INTEGER NOT NULL,
                item_count INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(batch_id) REFERENCES sample_batches(id),
                FOREIGN KEY(subject_id) REFERENCES subjects(id)
            );
            -- 证据包条目
            CREATE TABLE IF NOT EXISTS pack_items(
                id INTEGER PRIMARY KEY,
                pack_id INTEGER NOT NULL,
                business_key TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                item_fingerprint TEXT NOT NULL,
                FOREIGN KEY(pack_id) REFERENCES evidence_packs(id)
            );
            -- 校验清单结果
            CREATE TABLE IF NOT EXISTS checklist_results(
                id INTEGER PRIMARY KEY,
                pack_id INTEGER NOT NULL,
                check_code TEXT NOT NULL,
                check_title TEXT NOT NULL,
                passed INTEGER NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(pack_id) REFERENCES evidence_packs(id)
            );
            -- 放行决策
            CREATE TABLE IF NOT EXISTS release_decisions(
                id INTEGER PRIMARY KEY,
                pack_id INTEGER NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(pack_id) REFERENCES evidence_packs(id)
            );
            -- 设置
            CREATE TABLE IF NOT EXISTS settings(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            -- 采样 / 换代审计事件
            CREATE TABLE IF NOT EXISTS audit_events(
                id INTEGER PRIMARY KEY,
                event_type TEXT NOT NULL,
                subject_id INTEGER,
                batch_id INTEGER,
                pack_id INTEGER,
                detail_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            -- 影子试跑（空挂，后题用）
            CREATE TABLE IF NOT EXISTS shadow_runs(
                id INTEGER PRIMARY KEY,
                pack_id INTEGER,
                rule_version_id INTEGER,
                status TEXT,
                note TEXT,
                created_at TEXT
            );
            -- 规则版本（空挂，后题用）
            CREATE TABLE IF NOT EXISTS rule_versions(
                id INTEGER PRIMARY KEY,
                code TEXT,
                title TEXT,
                payload_json TEXT,
                active INTEGER DEFAULT 0,
                created_at TEXT
            );
            """
        )
        _migrate(conn)
        if conn.execute("SELECT COUNT(*) c FROM subjects").fetchone()["c"] == 0:
            now = _now()
            for s in SUBJECT_SEEDS:
                conn.execute(
                    """
                    INSERT INTO subjects(
                        code, title, domain, fixture, sample_rate_default, enabled, created_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        s["code"],
                        s["title"],
                        s["domain"],
                        s["fixture"],
                        s["sample_rate_default"],
                        s["enabled"],
                        now,
                    ),
                )
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                (key, value),
            )
        # 移除历史上未接线的 sample_mode（随机/确定性冒充开关），
        # 避免“不可复现随机”被误读成固定种子。
        conn.execute("DELETE FROM settings WHERE key='sample_mode'")
        conn.commit()
    finally:
        conn.close()
