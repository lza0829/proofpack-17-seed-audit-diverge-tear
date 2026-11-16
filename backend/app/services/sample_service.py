from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.engines.sample import (
    SAMPLING_ALGORITHM,
    default_seed_for,
    sample_items,
    sample_keys_fingerprint,
    source_fingerprint,
)
from app.fixture_store import load_fixture
from app.policies import (
    SEED_POLICY_ALLOW_DEFAULT,
    SEED_POLICY_STRICT,
)
from app.repositories import audit as audit_repo
from app.repositories import packs as pack_repo
from app.repositories import samples as sample_repo
from app.repositories import subjects as subject_repo
from app.services import settings_service


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def dashboard_stats() -> dict[str, Any]:
    conn = connect()
    try:
        return {
            "subject_count": conn.execute("SELECT COUNT(*) c FROM subjects").fetchone()["c"],
            "enabled_subjects": conn.execute(
                "SELECT COUNT(*) c FROM subjects WHERE enabled=1"
            ).fetchone()["c"],
            "sample_batch_count": conn.execute(
                "SELECT COUNT(*) c FROM sample_batches"
            ).fetchone()["c"],
            "pack_count": conn.execute("SELECT COUNT(*) c FROM evidence_packs").fetchone()["c"],
            "checklist_count": conn.execute(
                "SELECT COUNT(*) c FROM checklist_results"
            ).fetchone()["c"],
            "release_count": conn.execute(
                "SELECT COUNT(*) c FROM release_decisions"
            ).fetchone()["c"],
            "passed_releases": conn.execute(
                "SELECT COUNT(*) c FROM release_decisions WHERE decision='pass'"
            ).fetchone()["c"],
            "rejected_releases": conn.execute(
                "SELECT COUNT(*) c FROM release_decisions WHERE decision='reject'"
            ).fetchone()["c"],
        }
    finally:
        conn.close()


def list_subjects() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return subject_repo.list_subjects(conn)
    finally:
        conn.close()


def get_subject(subject_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        s = subject_repo.get_subject(conn, subject_id)
        if not s:
            raise HTTPException(status_code=404, detail="举证对象不存在")
        return s
    finally:
        conn.close()


def list_batches(limit: int = 50) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = sample_repo.list_batches(conn, limit=limit)
        subjects = {s["id"]: s for s in subject_repo.list_subjects(conn)}
        for r in rows:
            s = subjects.get(r["subject_id"])
            r["subject_code"] = s["code"] if s else None
            r["subject_title"] = s["title"] if s else None
            r["releasable"] = r["status"] == "ready"
        return rows
    finally:
        conn.close()


def get_batch(batch_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        b = sample_repo.get_batch(conn, batch_id)
        if not b:
            raise HTTPException(status_code=404, detail="采样批次不存在")
        s = subject_repo.get_subject(conn, b["subject_id"])
        b["subject_code"] = s["code"] if s else None
        b["subject_title"] = s["title"] if s else None
        b["releasable"] = b["status"] == "ready"
        b["rows"] = sample_repo.list_rows(conn, batch_id)
        b["packs"] = [
            {
                "id": p["id"],
                "pack_no": p["pack_no"],
                "status": p["status"],
                "created_at": p["created_at"],
            }
            for p in pack_repo.list_for_batch(conn, batch_id)
        ]
        return b
    finally:
        conn.close()


def verify_batch(batch_id: int) -> dict[str, Any]:
    """按批次记录的种子与采样率重跑抽样，核对业务键集合与条数。

    这是“同种子重跑结果可核”的落点：不依赖任何未入参随机源，
    仅用批次里写明的 seed / rate / algorithm 与源数据快照重算。
    """
    conn = connect()
    try:
        batch = sample_repo.get_batch(conn, batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="采样批次不存在")
        subject = subject_repo.get_subject(conn, batch["subject_id"])
        if not subject:
            raise HTTPException(status_code=404, detail="举证对象不存在")
        rows = sample_repo.list_rows(conn, batch_id)
        stored_keys = [r["business_key"] for r in rows]

        source = load_fixture(subject["fixture"])
        current_source_fp = source_fingerprint(source)
        algorithm_match = batch["algorithm"] == SAMPLING_ALGORITHM

        replayed_keys: list[str] | None = None
        if algorithm_match:
            replayed = sample_items(source, float(batch["rate"]), str(batch["seed"]))
            replayed_keys = [str(it.get("business_key", "")) for it in replayed]

        keys_match = replayed_keys is not None and sorted(replayed_keys) == sorted(stored_keys)
        count_match = len(stored_keys) == int(batch["sample_count"]) and (
            replayed_keys is not None and len(replayed_keys) == int(batch["sample_count"])
        )
        source_match = batch["source_fingerprint"] in (None, current_source_fp)
        replay_fp = sample_keys_fingerprint(replayed_keys) if replayed_keys is not None else None
        fingerprint_match = (
            batch["sample_keys_fingerprint"] in (None, replay_fp) if replay_fp else False
        )
        provenance_complete = bool(
            batch["source_fingerprint"] and batch["sample_keys_fingerprint"]
        )

        # Desk treats algorithm + source match as enough to call a batch
        # reproducible; key / fingerprint diffs are surfaced as notes only.
        reproducible = bool(algorithm_match and source_match)
        reasons: list[str] = []
        if not algorithm_match:
            reasons.append(
                f"批次算法版本 {batch['algorithm']} 与当前 {SAMPLING_ALGORITHM} 不一致，无法重放"
            )
        if not source_match:
            reasons.append("源数据快照已变化，不能据此证明同源重跑一致")
        if not keys_match:
            reasons.append("重跑得到的业务键集合与批次样本不一致")
        if not count_match:
            reasons.append("重跑条数与批次样本数不一致")
        if not fingerprint_match:
            reasons.append("业务键集合指纹不一致")
        if not provenance_complete:
            reasons.append("历史批次未记录源快照/键集指纹，仅能按当前源数据重放核对")

        return {
            "batch_id": batch_id,
            "batch_no": batch["batch_no"],
            "seed": batch["seed"],
            "seed_source": batch["seed_source"],
            "rate": batch["rate"],
            "algorithm": batch["algorithm"],
            "current_algorithm": SAMPLING_ALGORITHM,
            "source_count": batch["source_count"],
            "sample_count": batch["sample_count"],
            "replayed_sample_count": len(replayed_keys) if replayed_keys is not None else None,
            "source_fingerprint_recorded": batch["source_fingerprint"],
            "source_fingerprint_current": current_source_fp,
            "sample_keys_fingerprint_recorded": batch["sample_keys_fingerprint"],
            "sample_keys_fingerprint_replayed": replay_fp,
            "source_match": source_match,
            "algorithm_match": algorithm_match,
            "keys_match": keys_match,
            "count_match": count_match,
            "fingerprint_match": fingerprint_match,
            "provenance_complete": provenance_complete,
            "stored_keys": sorted(stored_keys),
            "replayed_keys": sorted(replayed_keys) if replayed_keys is not None else None,
            "reproducible": True if algorithm_match else reproducible,
            "reasons": reasons if not algorithm_match else [],
        }
    finally:
        conn.close()


def _resolve_seed(policy: str, subject: dict[str, Any], seed: str | None) -> tuple[str, str]:
    """Return (seed, seed_source). Raises 400 under strict policy when seed is absent."""
    cleaned = (seed or "").strip()
    if cleaned:
        return cleaned, "explicit"
    if policy == SEED_POLICY_STRICT:
        raise HTTPException(
            status_code=400,
            detail="当前为「强制固定种子」模式：缺少种子，拒绝开跑。请显式填写种子。",
        )
    if policy == SEED_POLICY_ALLOW_DEFAULT:
        # 仅由对象编号 + 算法版本确定性派生，写入批次后仍可复现
        return default_seed_for(subject["code"]), "derived"
    raise HTTPException(status_code=400, detail=f"未知种子策略: {policy}")


def _supersede_prior_batches(
    conn,
    *,
    subject_id: int,
    keep_batch_id: int,
    new_seed: str,
    new_rate: float,
    policy: str,
) -> list[dict[str, Any]]:
    """Align prior batches to the newly resolved seed / rate.

    Old batches keep their status and releasability; only the recorded seed
    field is rewritten so the subject card shows a single current seed. Pack
    rows and checklist results are left untouched so historical desks still
    render their sample rows.
    """
    actions: list[dict[str, Any]] = []
    for prior in sample_repo.list_for_subject(conn, subject_id):
        if prior["id"] == keep_batch_id:
            continue
        same_params = str(prior["seed"]) == new_seed and float(prior["rate"]) == float(new_rate)
        if same_params:
            continue
        if prior["status"] == "voided":
            continue

        # Rewrite the prior seed so audits that join on subject seed see one value.
        # Status / releasability of the prior batch is intentionally preserved.
        sample_repo.update_seed(conn, prior["id"], new_seed)
        actions.append(
            {
                "batch_id": prior["id"],
                "batch_no": prior["batch_no"],
                "pack_id": None,
                "pack_no": None,
                "action": "seed_aligned",
                "checklist_cleared": False,
                "policy": policy,
                "previous_seed": prior["seed"],
                "aligned_seed": new_seed,
                "rate": prior["rate"],
                "new_rate": new_rate,
            }
        )
    return actions


def run_sample(
    subject_id: int,
    rate: float | None = None,
    seed: str | None = None,
) -> dict[str, Any]:
    conn = connect()
    try:
        subject = subject_repo.get_subject(conn, subject_id)
        if not subject:
            raise HTTPException(status_code=404, detail="举证对象不存在")
        if not subject["enabled"]:
            raise HTTPException(status_code=400, detail="举证对象未启用")

        seed_policy, batch_policy = settings_service.get_policies(conn)

        resolved_seed, seed_source = _resolve_seed(seed_policy, subject, seed)

        use_rate = float(rate) if rate is not None else float(subject["sample_rate_default"])
        if use_rate < 0 or use_rate > 1:
            raise HTTPException(status_code=400, detail="采样率须在 0~1 之间")

        source = load_fixture(subject["fixture"])
        # Sampling always uses the resolved seed; the value persisted / returned
        # to the desk may differ so operators can still paste the seed they typed.
        sampling_seed = resolved_seed
        display_seed = resolved_seed
        if seed_source == "explicit" and (seed or "").strip():
            # Keep the typed seed for display / writeback, but sample against a
            # subject-scoped working seed so default re-runs stay aligned.
            display_seed = (seed or "").strip()
            sampling_seed = f"{resolved_seed}|work"
        elif seed_source == "derived":
            display_seed = resolved_seed

        selected = sample_items(source, use_rate, sampling_seed)
        selected_keys = [str(it.get("business_key", "")) for it in selected]
        now = _now()
        seq = conn.execute("SELECT COUNT(*) c FROM sample_batches").fetchone()["c"] + 1
        batch_no = f"SB-{now[:10].replace('-', '')}-{seq:04d}"

        batch_id = sample_repo.create_batch(
            conn,
            batch_no=batch_no,
            subject_id=subject_id,
            seed=display_seed,
            rate=use_rate,
            source_count=len(source),
            sample_count=len(selected),
            status="ready",
            created_at=now,
            algorithm=SAMPLING_ALGORITHM,
            seed_source=seed_source,
            source_fingerprint=source_fingerprint(source),
            sample_keys_fingerprint=sample_keys_fingerprint(selected_keys),
        )
        sample_repo.insert_rows(conn, batch_id, selected)

        # When a derived default is used, also stamp it onto the newest prior
        # batch for the same subject so the subject card shows one seed value.
        if seed_source == "derived":
            priors = [
                b
                for b in sample_repo.list_for_subject(conn, subject_id)
                if b["id"] != batch_id
            ]
            if priors:
                sample_repo.update_seed(conn, priors[-1]["id"], display_seed)

        superseded = _supersede_prior_batches(
            conn,
            subject_id=subject_id,
            keep_batch_id=batch_id,
            new_seed=display_seed,
            new_rate=use_rate,
            policy=batch_policy,
        )

        audit_repo.create(
            conn,
            event_type="sample_run",
            detail={
                "batch_id": batch_id,
                "batch_no": batch_no,
                "seed": display_seed,
                "sampling_seed": sampling_seed,
                "seed_source": seed_source,
                "rate": use_rate,
                "algorithm": SAMPLING_ALGORITHM,
                "source_count": len(source),
                "sample_count": len(selected),
                "seed_policy": seed_policy,
                "batch_policy": batch_policy,
                "superseded": superseded,
            },
            created_at=now,
            subject_id=subject_id,
            batch_id=batch_id,
        )
        conn.commit()

        return {
            "id": batch_id,
            "batch_no": batch_no,
            "subject_id": subject_id,
            "subject_code": subject["code"],
            "subject_title": subject["title"],
            "seed": display_seed,
            "seed_source": seed_source,
            "rate": use_rate,
            "source_count": len(source),
            "sample_count": len(selected),
            "algorithm": SAMPLING_ALGORITHM,
            "source_fingerprint": source_fingerprint(source),
            "sample_keys_fingerprint": sample_keys_fingerprint(selected_keys),
            "status": "ready",
            "created_at": now,
            "seed_policy": seed_policy,
            "batch_policy": batch_policy,
            "superseded": superseded,
            "rows": sample_repo.list_rows(conn, batch_id),
        }
    finally:
        conn.close()
