# 05-proofpack（核证匣）

Proofpack — 举证放行：采样抽取 → 证据打包 → 校验清单 → 放行/驳回闭环。

与 Feedstitch（多源归并）、Batchgate（作业编排）、Syncledger（双端对账）、Changecast（CDC 投影）主链路不同。夹具全本地，不连活库。

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5500 |
| API | http://localhost:10500 |

## 主链

选举证对象 → 按固定种子采样 → 生成证据包（含指纹）→ 跑基础校验清单 → 全过才可放行，否则驳回并留痕

## 可复现采样约束

同一举证对象、同一采样率、同一种子，连续两次采样必须得到完全一致的业务键集合与条数。
抽样判定只吃 `(seed, business_key)`——对二者求 SHA-256 取桶值，不读墙钟、不读进程熵。
算法版本记为 `sha256-bucket-v1`；版本变化时批次会被判定为不可重放。

每个采样批次写明举证要素：**种子、采样率、源条数、样本数**，另记
`seed_source`（显式 / 派生）、源快照指纹、样本键集指纹与算法版本。
`GET /api/samples/{id}/verify` 会用批次记录的种子与采样率重跑，逐项核对键集、条数与指纹，
给出 `reproducible` 与不一致原因——这就是“同种子重跑结果可核”的落点。

### 互斥策略（设置页可切换，变更留痕）

**种子策略**（缺种子时如何处置）

| 取值 | 语义 |
| --- | --- |
| `strict`（默认） | 强制固定种子；缺种子直接拒绝开跑 |
| `allow_default` | 允许缺省种子；按对象编号 + 算法版本确定性派生，实际采用的种子写回批次，仍可复现 |

**换代策略**（改种子或改采样率后，如何处置未放行的旧证据）

| 取值 | 语义 |
| --- | --- |
| `retain`（默认） | 旧批次与未放行旧包降为只读对照（`reference`），新采样另开批次；旧包不得沿用新样本 |
| `void` | 作废关联的未放行旧包及其清单（`voided`），强制按新种子重打包后方可继续 |

两种策略都只处置**未放行**证据：已放行证据保留原样，但不会被新种子样本套用。
旧批次同时退出可打包路径，因此新旧种子对应的未放行包不可能同时保持可放行——
打包与放行两条入口都会拦下 `reference` / `voided`。

## 种子举证对象

- 门店日结抽检
- 渠道结算抽检
- 价目变更核证

底座内置基础校验：条数一致、键唯一、必填非空。影子试跑 / 规则版本表空挂，深逻辑留给后题。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。

## 本地后端

```bash
cd backend
pip install -r requirements.txt
set DATA_DIR=%TEMP%\proofpack-data
uvicorn app.main:app --host 127.0.0.1 --port 10500
```

## 测试

```bash
cd backend
pip install -r requirements.txt httpx
pytest tests -q
```

