from __future__ import annotations

import hashlib
import json
import math
from typing import Any

# 采样算法版本：一旦发布，抽样判定语义不得静默改变。
# sha256-bucket-v1 = 对 (seed, business_key) 求 sha256，取前 12 位十六进制折算为 [0,1) 桶值；
# 桶值 < rate 者入样，结果按 business_key 排序。空结果时按桶值升序补足 ceil(rate*n) 条。
SAMPLING_ALGORITHM = "sha256-bucket-v1"


def _bucket(seed: str, business_key: str) -> float:
    """0..1 bucket derived ONLY from (seed, business_key).

    抽样判定只消费这两个入参：不读墙钟、不读进程熵、不读调用顺序。
    同一 (seed, business_key) 在任何进程、任何时刻、任何调用顺序下
    都必须得到同一桶值——这是「固定种子可复现」的根基。
    """
    digest = hashlib.sha256(f"{seed}|{business_key}".encode("utf-8")).hexdigest()
    return int(digest[:12], 16) / float(16**12)


def source_fingerprint(items: list[dict[str, Any]]) -> str:
    """Stable fingerprint over the full source payload.

    Used to prove that a batch was sampled from the same source snapshot when it is
    re-verified later. Sorting by business_key keeps the digest independent of the
    physical row order of the source.
    """
    ordered = sorted(items, key=lambda x: str(x.get("business_key", "")))
    payload = json.dumps(ordered, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sample_keys_fingerprint(keys: list[str]) -> str:
    """Fingerprint over the selected business-key set (sorted, one per line)."""
    joined = "\n".join(sorted(str(k) for k in keys))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def default_seed_for(subject_code: str) -> str:
    """Deterministic default seed for the allow-default policy.

    Deliberately derived only from the subject code and the sampling algorithm
    version — no wall clock, no process entropy. The exact value is written back to
    the batch, so a later run can reproduce it even if the derivation changes.
    """
    digest = hashlib.sha256(f"{subject_code}|{SAMPLING_ALGORITHM}".encode("utf-8")).hexdigest()
    return f"auto:{subject_code}:{digest[:16]}"


def _order_key(item: dict[str, Any]) -> tuple[str, str]:
    """Deterministic presentation order: by business_key, then full payload.

    只依赖入参数据本身，与输入物理顺序无关——同一源快照无论正序倒序传入，
    输出逐字节一致。
    """
    return (
        str(item.get("business_key", "")),
        json.dumps(item, ensure_ascii=False, sort_keys=True),
    )


def sample_items(
    items: list[dict[str, Any]],
    rate: float,
    seed: str,
) -> list[dict[str, Any]]:
    """
    Sampling by seed.
    Items whose bucket < rate are included; the bucket value depends only on
    (seed, business_key). Empty selections fall back to the lowest-bucket
    ceil(rate*n) items, ranked deterministically. Output is sorted by
    business_key so the same (source, rate, seed) always yields the same
    key set in the same order.
    Ensures at least one item when source is non-empty and rate > 0.
    """
    if not items:
        return []
    rate = max(0.0, min(1.0, float(rate)))
    if rate <= 0:
        return []
    if rate >= 1.0:
        return sorted(items, key=_order_key)

    selected = [
        item
        for item in items
        if _bucket(seed, str(item.get("business_key", ""))) < rate
    ]
    if not selected:
        # 空结果回退：按 (桶值, 业务键, 负载) 确定性升序补足，同样只读入参。
        ranked = sorted(
            items,
            key=lambda x: (
                _bucket(seed, str(x.get("business_key", ""))),
                _order_key(x),
            ),
        )
        n = max(1, min(len(items), math.ceil(rate * len(items))))
        selected = ranked[:n]

    return sorted(selected, key=_order_key)
