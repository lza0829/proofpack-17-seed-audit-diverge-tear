from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.engines.sample import SAMPLING_ALGORITHM
from app.repositories import checklist as check_repo
from app.repositories import packs as pack_repo
from app.repositories import releases as release_repo
from app.repositories import samples as sample_repo
from app.repositories import settings as settings_repo
from app.services import sample_service


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def list_releases(limit: int = 50) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = release_repo.list_decisions(conn, limit=limit)
        packs = {p["id"]: p for p in pack_repo.list_packs(conn, limit=500)}
        for r in rows:
            p = packs.get(r["pack_id"])
            r["pack_no"] = p["pack_no"] if p else None
            r["pack_status"] = p["status"] if p else None
        return rows
    finally:
        conn.close()


def decide(pack_id: int, decision: str, reason: str | None = None) -> dict[str, Any]:
    decision = (decision or "").strip().lower()
    if decision not in ("pass", "reject"):
        raise HTTPException(status_code=400, detail="决策须为 pass 或 reject")

    conn = connect()
    try:
        pack = pack_repo.get_pack(conn, pack_id)
        if not pack:
            raise HTTPException(status_code=404, detail="证据包不存在")

        if pack["status"] in ("voided", "reference"):
            label = "已作废包" if pack["status"] == "voided" else "只读对照包"
            raise HTTPException(
                status_code=409,
                detail=f"该证据包为{label}，不得放行；请按当前批次重新生成证据包。",
            )

        batch = sample_repo.get_batch(conn, pack["batch_id"])
        if not batch:
            raise HTTPException(
                status_code=409,
                detail="证据包关联的采样批次不存在，不得放行。",
            )
        if batch["algorithm"] != SAMPLING_ALGORITHM:
            raise HTTPException(
                status_code=409,
                detail="证据包关联的采样批次算法版本与当前不一致，不得放行。",
            )
        if batch["status"] != "ready":
            raise HTTPException(
                status_code=409,
                detail="证据包关联的采样批次已退出可放行路径（只读对照或已作废），不得放行。",
            )
        # 种子 / 采样率时效检查：批次参数必须仍是该对象当前参数，
        # 改种或改率之后的旧样本包不得再放行。
        latest = sample_repo.latest_ready_for_subject(conn, batch["subject_id"])
        if latest and (
            str(latest["seed"]) != str(batch["seed"])
            or float(latest["rate"]) != float(batch["rate"])
        ):
            raise HTTPException(
                status_code=409,
                detail="证据包关联批次的种子或采样率已非该对象当前参数，不得放行；请按最新批次重新采样打包。",
            )

        results = check_repo.list_results(conn, pack_id=pack_id)
        if not results:
            raise HTTPException(status_code=400, detail="请先跑校验清单")

        all_ok = check_repo.all_passed(conn, pack_id)
        require = settings_repo.get_setting(conn, "require_checklist_pass", "1") == "1"

        if decision == "pass":
            # 同一种子同一采样率核对：批次记录的种子必须能重放出批次样本键集，
            # 核对通过前不得放行通过。核对失败即发现「记录种子 ≠ 实际抽样」，
            # 当场执行互斥收口：整批作废并禁止放行。
            stored_keys = [
                r["business_key"] for r in sample_repo.list_rows(conn, batch["id"])
            ]
            replayed_keys = sample_service.replay_batch_keys(conn, batch)
            recheck_ok = (
                replayed_keys is not None
                and sorted(replayed_keys) == sorted(stored_keys)
                and len(stored_keys) == int(batch["sample_count"])
            )
            if not recheck_ok:
                sample_service.enact_divergence_closure(
                    conn,
                    batch=batch,
                    reason="放行前同种子同率核对未通过：记录种子无法重放出批次样本键集",
                    now=_now(),
                )
                conn.commit()
                raise HTTPException(
                    status_code=409,
                    detail="同种子同率核对未通过：该批次记录种子与实际抽样不一致，"
                    "已整批作废该批次及关联未放行包与未决清单；须按明示种子重新采样。",
                )
            if require and not all_ok:
                raise HTTPException(
                    status_code=400,
                    detail="清单未全部通过，不能放行",
                )
            if not reason:
                reason = "清单全部通过，予以放行"
            status = "released"
        else:
            if not reason or not str(reason).strip():
                raise HTTPException(status_code=400, detail="驳回须填写原因")
            status = "rejected"

        now = _now()
        rid = release_repo.create_decision(
            conn,
            pack_id=pack_id,
            decision=decision,
            reason=reason,
            created_at=now,
        )
        pack_repo.update_status(conn, pack_id, status)
        conn.commit()

        return {
            "id": rid,
            "pack_id": pack_id,
            "pack_no": pack["pack_no"],
            "decision": decision,
            "reason": reason,
            "created_at": now,
            "checklist_all_passed": all_ok,
            "batch_seed": batch["seed"],
            "batch_status": batch["status"],
        }
    finally:
        conn.close()
