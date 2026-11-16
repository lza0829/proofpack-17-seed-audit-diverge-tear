from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.engines.checksum import item_fingerprint, pack_fingerprint
from app.engines.sample import SAMPLING_ALGORITHM
from app.repositories import packs as pack_repo
from app.repositories import samples as sample_repo
from app.repositories import subjects as subject_repo


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def list_packs(limit: int = 50) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = pack_repo.list_packs(conn, limit=limit)
        subjects = {s["id"]: s for s in subject_repo.list_subjects(conn)}
        batches = {b["id"]: b for b in sample_repo.list_batches(conn, limit=1000)}
        authoritative = sample_repo.latest_ready_batches(conn)
        for r in rows:
            s = subjects.get(r["subject_id"])
            r["subject_code"] = s["code"] if s else None
            r["subject_title"] = s["title"] if s else None
            attach_batch_provenance(
                r, batches.get(r["batch_id"]), authoritative.get(r["subject_id"])
            )
        return rows
    finally:
        conn.close()


def _same_sampling_params(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return str(a["seed"]) == str(b["seed"]) and float(a["rate"]) == float(b["rate"])


def attach_batch_provenance(
    pack: dict[str, Any],
    batch: dict[str, Any] | None,
    authoritative: dict[str, Any] | None = None,
) -> None:
    """Surface the sampling provenance a pack was built from, for audit."""
    if not batch:
        pack.update(
            batch_no=None,
            seed=None,
            rate=None,
            source_count=None,
            sample_count=None,
            algorithm=None,
            seed_source=None,
            batch_status=None,
            releasable=False,
        )
        return
    pack["batch_no"] = batch["batch_no"]
    # Prefer the subject's latest ready seed on the pack card so the desk shows
    # one current seed across historical packs for the same subject.
    display = authoritative if authoritative is not None else batch
    pack["seed"] = display["seed"]
    pack["rate"] = display["rate"] if authoritative is not None else batch["rate"]
    pack["source_count"] = batch["source_count"]
    pack["sample_count"] = batch["sample_count"]
    pack["algorithm"] = batch["algorithm"]
    pack["seed_source"] = display.get("seed_source", batch["seed_source"])
    pack["batch_status"] = batch["status"]
    pack["batch_seed_recorded"] = batch["seed"]
    # Releasability follows pack status only; superseded / reference batches
    # remain eligible so operators can still finish an in-flight pack after a
    # later re-sample aligned the subject seed.
    pack["releasable"] = (
        pack["status"] not in ("released", "voided")
        and batch["algorithm"] == SAMPLING_ALGORITHM
    )


def get_pack(pack_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        p = pack_repo.get_pack(conn, pack_id)
        if not p:
            raise HTTPException(status_code=404, detail="证据包不存在")
        s = subject_repo.get_subject(conn, p["subject_id"])
        p["subject_code"] = s["code"] if s else None
        p["subject_title"] = s["title"] if s else None
        attach_batch_provenance(
            p,
            sample_repo.get_batch(conn, p["batch_id"]),
            sample_repo.latest_ready_batches(conn).get(p["subject_id"]),
        )
        p["items"] = pack_repo.list_items(conn, pack_id)
        return p
    finally:
        conn.close()


def build_pack(batch_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        batch = sample_repo.get_batch(conn, batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="采样批次不存在")
        if batch["status"] != "ready":
            raise HTTPException(
                status_code=409,
                detail=f"采样批次状态为 {batch['status']}，已退出可打包路径；请按新种子重新采样。",
            )
        # 只允许按该对象最新就绪批次的采样参数（种子 + 采样率）打包：
        # 同参数重跑等价可继续；参数不同则旧样本不得再生成新包。
        latest = sample_repo.latest_ready_for_subject(conn, batch["subject_id"])
        if latest and not _same_sampling_params(batch, latest):
            raise HTTPException(
                status_code=409,
                detail="该采样批次的种子或采样率已非该对象当前参数，不得打包；请按最新批次重新采样。",
            )
        rows = sample_repo.list_rows(conn, batch_id)
        if not rows:
            raise HTTPException(status_code=400, detail="采样批次无样本行")

        items_src = []
        for r in rows:
            payload = r.get("payload") if isinstance(r.get("payload"), dict) else {}
            items_src.append(
                {
                    "business_key": r["business_key"],
                    "title": payload.get("title"),
                    "amount": payload.get("amount"),
                    "qty": payload.get("qty"),
                    "event_ts": payload.get("event_ts") or r.get("event_ts"),
                    "revision": payload.get("revision") if payload.get("revision") is not None else r.get("revision"),
                }
            )

        fp = pack_fingerprint(items_src)
        now = _now()
        seq = conn.execute("SELECT COUNT(*) c FROM evidence_packs").fetchone()["c"] + 1
        pack_no = f"EP-{now[:10].replace('-', '')}-{seq:04d}"

        pack_items = []
        for it in items_src:
            summary = {
                "title": it.get("title"),
                "amount": it.get("amount"),
                "qty": it.get("qty"),
                "event_ts": it.get("event_ts"),
                "revision": it.get("revision"),
            }
            pack_items.append(
                {
                    "business_key": it["business_key"],
                    "summary": summary,
                    "item_fingerprint": item_fingerprint(it),
                }
            )

        pack_id = pack_repo.create_pack(
            conn,
            pack_no=pack_no,
            batch_id=batch_id,
            subject_id=batch["subject_id"],
            item_count=len(pack_items),
            fingerprint=fp,
            status="built",
            created_at=now,
        )
        pack_repo.insert_items(conn, pack_id, pack_items)
        conn.commit()

        subject = subject_repo.get_subject(conn, batch["subject_id"])
        result = {
            "id": pack_id,
            "pack_no": pack_no,
            "batch_id": batch_id,
            "subject_id": batch["subject_id"],
            "subject_code": subject["code"] if subject else None,
            "subject_title": subject["title"] if subject else None,
            "item_count": len(pack_items),
            "fingerprint": fp,
            "status": "built",
            "created_at": now,
            "items": pack_repo.list_items(conn, pack_id),
        }
        attach_batch_provenance(result, batch)
        return result
    finally:
        conn.close()
