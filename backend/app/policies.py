"""互斥策略的唯一来源：路由层、服务层、前端文案都以此为准。"""
from __future__ import annotations

# 种子策略：缺种子时如何处置
SEED_POLICY_STRICT = "strict"  # 强制固定种子，缺种子拒绝开跑
SEED_POLICY_ALLOW_DEFAULT = "allow_default"  # 允许缺省种子，派生种子写回批次
SEED_POLICIES = (SEED_POLICY_STRICT, SEED_POLICY_ALLOW_DEFAULT)

# 换代策略：改种子/采样率后如何处置未放行旧证据
BATCH_POLICY_RETAIN = "retain"  # 旧批次只读对照，新采样另开批次
BATCH_POLICY_VOID = "void"  # 作废未放行旧包及清单，强制重打包
BATCH_POLICIES = (BATCH_POLICY_RETAIN, BATCH_POLICY_VOID)

DEFAULT_SEED_POLICY = SEED_POLICY_STRICT
DEFAULT_BATCH_POLICY = BATCH_POLICY_RETAIN

# 设置页可改写的键
WRITABLE_SETTINGS = {
    "seed_policy": SEED_POLICIES,
    "batch_policy": BATCH_POLICIES,
}
