from __future__ import annotations

import hashlib
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.config
    import app.db
    import app.seed
    import app.main

    importlib.reload(app.config)
    importlib.reload(app.db)
    importlib.reload(app.seed)
    importlib.reload(app.main)
    with TestClient(app.main.app) as c:
        yield c


def keys_of(batch: dict) -> list[str]:
    return sorted(r["business_key"] for r in batch["rows"])


def run(client, subject_id=1, rate=0.3, seed="seed-A"):
    body = {"subject_id": subject_id, "rate": rate}
    if seed is not None:
        body["seed"] = seed
    return client.post("/api/samples/run", json=body)


def test_same_seed_same_rate_is_byte_identical(client):
    a = run(client).json()
    b = run(client).json()
    assert a["seed"] == b["seed"] == "seed-A"
    assert a["rate"] == b["rate"] == 0.3
    assert keys_of(a) == keys_of(b)
    assert a["sample_count"] == b["sample_count"] == len(keys_of(a))
    assert a["source_count"] == b["source_count"]
    assert a["sample_keys_fingerprint"] == b["sample_keys_fingerprint"]


def test_batch_records_seed_rate_source_and_sample_counts(client):
    d = run(client, subject_id=3, rate=0.5, seed="s-1").json()
    assert d["seed"] == "s-1"
    assert d["seed_source"] == "explicit"
    assert d["rate"] == 0.5
    assert d["source_count"] == 12  # price_change fixture
    assert d["sample_count"] == len(d["rows"])
    assert d["algorithm"]
    listed = next(b for b in client.get("/api/samples").json() if b["id"] == d["id"])
    assert listed["seed"] == "s-1"
    assert listed["rate"] == 0.5
    assert listed["source_count"] == 12
    assert listed["sample_count"] == d["sample_count"]


def test_verify_replays_identical_key_set(client):
    d = run(client).json()
    v = client.get(f"/api/samples/{d['id']}/verify").json()
    assert v["reproducible"] is True
    assert v["keys_match"] is True
    assert v["count_match"] is True
    assert v["replayed_keys"] == sorted(keys_of(d))
    assert v["sample_keys_fingerprint_replayed"] == d["sample_keys_fingerprint"]


def test_different_seed_changes_selection(client):
    seen = set()
    for seed in ("seed-A", "seed-B", "seed-C"):
        d = run(client, rate=0.4, seed=seed).json()
        seen.add(tuple(keys_of(d)))
    assert len(seen) > 1, "不同种子应产生不同样本集合，否则种子形同虚设"


def test_strict_policy_refuses_missing_seed(client):
    assert client.get("/api/settings").json()["seed_policy"] == "strict"
    r = run(client, seed=None)
    assert r.status_code == 400
    assert "拒绝开跑" in r.json()["detail"]
    assert client.get("/api/samples").json() == []


def test_allow_default_records_derived_seed_and_is_reproducible(client):
    assert client.post("/api/settings", json={"seed_policy": "allow_default"}).status_code == 200
    d1 = run(client, seed=None).json()
    d2 = run(client, seed=None).json()
    assert d1["seed_source"] == "derived"
    assert d1["seed"], "派生种子必须非空并写回批次"
    assert d1["seed"] == d2["seed"]
    assert keys_of(d1) == keys_of(d2)
    listed = next(b for b in client.get("/api/samples").json() if b["id"] == d1["id"])
    assert listed["seed"] == d1["seed"]
    assert listed["seed_source"] == "derived"
    assert client.get(f"/api/samples/{d1['id']}/verify").json()["reproducible"] is True


def test_settings_policies_are_mutually_exclusive_enums(client):
    assert client.post("/api/settings", json={"seed_policy": "random"}).status_code == 400
    assert client.post("/api/settings", json={"batch_policy": "both"}).status_code == 400
    assert client.post("/api/settings", json={"default_sample_rate": "0.9"}).status_code == 400
    assert client.post("/api/settings", json={"seed_policy": "strict"}).status_code == 200
    assert client.get("/api/settings").json()["seed_policy"] == "strict"


def _build_and_pass(client, batch_id):
    pack = client.post("/api/packs/build", json={"batch_id": batch_id}).json()
    assert client.post("/api/checklist/run", json={"pack_id": pack["id"]}).json()["all_passed"] is True
    assert client.post(
        "/api/releases/decide", json={"pack_id": pack["id"], "decision": "pass"}
    ).status_code == 200
    return pack


def _build(client, batch_id):
    return client.post("/api/packs/build", json={"batch_id": batch_id}).json()


def test_retain_policy_keeps_old_batch_readonly_and_blocks_release(client):
    old = run(client, seed="old").json()
    released = _build_and_pass(client, old["id"])
    pending = _build(client, old["id"])
    client.post("/api/checklist/run", json={"pack_id": pending["id"]})

    new = run(client, seed="new").json()
    assert new["seed"] == "new"

    packs = {p["id"]: p for p in client.get("/api/packs").json()}
    assert packs[released["id"]]["status"] == "released"  # 已放行不受影响
    assert packs[pending["id"]]["status"] == "reference"
    assert packs[pending["id"]]["releasable"] is False
    # 旧批次退出可打包路径
    batches = {b["id"]: b for b in client.get("/api/samples").json()}
    assert batches[old["id"]]["status"] == "reference"

    # 只读对照包不得放行、不得重跑清单、不得再打包
    assert client.post(
        "/api/releases/decide", json={"pack_id": pending["id"], "decision": "pass"}
    ).status_code == 409
    assert client.post("/api/checklist/run", json={"pack_id": pending["id"]}).status_code == 409
    assert client.post("/api/packs/build", json={"batch_id": old["id"]}).status_code == 409

    # 新批次可正常打包放行
    fresh = _build(client, new["id"])
    assert fresh["seed"] == "new"
    client.post("/api/checklist/run", json={"pack_id": fresh["id"]})
    assert client.post(
        "/api/releases/decide", json={"pack_id": fresh["id"], "decision": "pass"}
    ).status_code == 200


def test_void_policy_voids_unreleased_packs_and_their_checklists(client):
    assert client.post("/api/settings", json={"batch_policy": "void"}).status_code == 200
    old = run(client, seed="old").json()
    released = _build_and_pass(client, old["id"])
    pending = _build(client, old["id"])
    assert len(client.get(f"/api/checklist?pack_id={pending['id']}").json()) == 0
    client.post("/api/checklist/run", json={"pack_id": pending["id"]})
    assert len(client.get(f"/api/checklist?pack_id={pending['id']}").json()) == 3

    new = run(client, seed="new").json()

    packs = {p["id"]: p for p in client.get("/api/packs").json()}
    assert packs[released["id"]]["status"] == "released"
    assert packs[pending["id"]]["status"] == "voided"
    assert packs[pending["id"]]["releasable"] is False
    # 作废包的清单被清空
    assert client.get(f"/api/checklist?pack_id={pending['id']}").json() == []
    # 已放行包的清单不受影响
    assert len(client.get(f"/api/checklist?pack_id={released['id']}").json()) == 3

    assert client.post(
        "/api/releases/decide", json={"pack_id": pending["id"], "decision": "pass"}
    ).status_code == 409
    assert client.post("/api/packs/build", json={"batch_id": old["id"]}).status_code == 409

    # 必须按新种子重打包后方可继续
    repacked = _build(client, new["id"])
    client.post("/api/checklist/run", json={"pack_id": repacked["id"]})
    assert client.post(
        "/api/releases/decide", json={"pack_id": repacked["id"], "decision": "pass"}
    ).status_code == 200


def test_new_and_old_seed_packs_never_both_releasable(client):
    for policy in ("retain", "void"):
        client.post("/api/settings", json={"batch_policy": policy})
        # 每轮换对象，避免前一策略留下的状态干扰
        subject_id = 1 if policy == "retain" else 2
        old = run(client, subject_id=subject_id, seed=f"{policy}-old").json()
        old_pack = _build(client, old["id"])
        client.post("/api/checklist/run", json={"pack_id": old_pack["id"]})
        new = run(client, subject_id=subject_id, seed=f"{policy}-new").json()
        new_pack = _build(client, new["id"])
        client.post("/api/checklist/run", json={"pack_id": new_pack["id"]})
        packs = {p["id"]: p for p in client.get("/api/packs").json()}
        assert not (packs[old_pack["id"]]["releasable"] and packs[new_pack["id"]]["releasable"])


def test_released_pack_is_not_marked_releasable(client):
    d = run(client, seed="rel").json()
    released = _build_and_pass(client, d["id"])
    packs = {p["id"]: p for p in client.get("/api/packs").json()}
    assert packs[released["id"]]["status"] == "released"
    assert packs[released["id"]]["releasable"] is False


def test_switching_retain_to_void_supersedes_existing_reference_packs(client):
    old = run(client, seed="old").json()
    pending = _build(client, old["id"])
    client.post("/api/checklist/run", json={"pack_id": pending["id"]})
    run(client, seed="mid").json()  # retain 下把 old 批次与旧包降为只读对照
    packs = {p["id"]: p for p in client.get("/api/packs").json()}
    assert packs[pending["id"]]["status"] == "reference"

    assert client.post("/api/settings", json={"batch_policy": "void"}).status_code == 200
    run(client, seed="new").json()  # 切到 void 后再换种子
    packs = {p["id"]: p for p in client.get("/api/packs").json()}
    assert packs[pending["id"]]["status"] == "voided"
    batches = {b["id"]: b for b in client.get("/api/samples").json()}
    assert batches[old["id"]]["status"] == "voided"


def test_rate_change_also_triggers_supersede(client):
    old = run(client, seed="same", rate=0.3).json()
    pending = _build(client, old["id"])
    client.post("/api/checklist/run", json={"pack_id": pending["id"]})
    run(client, seed="same", rate=0.8)
    packs = {p["id"]: p for p in client.get("/api/packs").json()}
    assert packs[pending["id"]]["status"] == "reference"


def test_legacy_two_ready_batches_do_not_both_allow_release(client, monkeypatch):
    """历史库里同对象可能残留多个 ready 批次：旧参数样本不得再打包/放行。"""
    from app.db import connect

    old = run(client, seed="ghost-old").json()
    stale_pack = _build(client, old["id"])
    client.post("/api/checklist/run", json={"pack_id": stale_pack["id"]})
    new = run(client, seed="ghost-new").json()

    # 模拟旧库：旧批次被手工置回 ready（跳过换代处置）
    conn = connect()
    try:
        conn.execute("UPDATE sample_batches SET status='ready' WHERE id=?", (old["id"],))
        conn.execute("UPDATE evidence_packs SET status='built' WHERE id=?", (stale_pack["id"],))
        conn.commit()
    finally:
        conn.close()

    packs = {p["id"]: p for p in client.get("/api/packs").json()}
    assert packs[stale_pack["id"]]["releasable"] is False
    assert client.post(
        "/api/releases/decide", json={"pack_id": stale_pack["id"], "decision": "pass"}
    ).status_code == 409
    assert client.post("/api/packs/build", json={"batch_id": old["id"]}).status_code == 409
    # 最新参数批次仍可正常打包放行
    fresh = _build(client, new["id"])
    assert fresh["releasable"] is True
    client.post("/api/checklist/run", json={"pack_id": fresh["id"]})
    assert client.post(
        "/api/releases/decide", json={"pack_id": fresh["id"], "decision": "pass"}
    ).status_code == 200


def test_rerun_with_same_params_keeps_batch_ready(client):
    first = run(client, seed="same", rate=0.3).json()
    second = run(client, seed="same", rate=0.3).json()
    batches = {b["id"]: b for b in client.get("/api/samples").json()}
    assert batches[first["id"]]["status"] == "ready"
    assert batches[second["id"]]["status"] == "ready"
    assert keys_of(first) == keys_of(second)


def test_sampling_determinism_depends_only_on_seed_and_key():
    """抽样判定只允许依赖 (seed, business_key)，与调用顺序和墙钟无关。"""
    from app.engines.sample import _bucket, sample_items
    from app.fixture_store import load_fixture

    src = load_fixture("store_dayend.json")
    assert _bucket("seed-A", "SD-1001") == _bucket("seed-A", "SD-1001")
    a = sample_items(list(reversed(src)), 0.4, "seed-A")
    b = sample_items(src, 0.4, "seed-A")
    assert [x["business_key"] for x in a] == [x["business_key"] for x in b]
    # 同键必同桶，且桶值只由 seed+key 决定
    for item in src:
        expect = hashlib.sha256(
            f"seed-A|{item['business_key']}".encode("utf-8")
        ).hexdigest()[:12]
        assert _bucket("seed-A", item["business_key"]) == int(expect, 16) / float(16**12)


def test_pack_carries_batch_provenance(client):
    d = run(client, subject_id=2, rate=0.5, seed="prov").json()
    pack = _build(client, d["id"])
    assert pack["seed"] == "prov"
    assert pack["rate"] == 0.5
    assert pack["batch_no"] == d["batch_no"]
    assert pack["source_count"] == 8
    assert pack["sample_count"] == d["sample_count"]
    assert pack["algorithm"]
    detail = client.get(f"/api/packs/{pack['id']}").json()
    assert detail["seed"] == "prov"
    assert detail["batch_status"] == "ready"
