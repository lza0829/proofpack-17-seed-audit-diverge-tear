from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import sample_service as svc

router = APIRouter(tags=["samples"])


class SampleRunBody(BaseModel):
    subject_id: int
    rate: float | None = Field(default=None, ge=0, le=1)
    # 不设默认值：种子是否可由系统派生由设置里的互斥策略决定，
    # 绝不在路由层静默塞一个“看起来固定”的种子。
    seed: str | None = None


@router.get("/samples")
def list_samples():
    return svc.list_batches()


@router.get("/samples/{batch_id}")
def get_sample(batch_id: int):
    return svc.get_batch(batch_id)


@router.get("/samples/{batch_id}/verify")
def verify_sample(batch_id: int):
    return svc.verify_batch(batch_id)


@router.post("/samples/run")
def run_sample(body: SampleRunBody) -> dict[str, Any]:
    return svc.run_sample(body.subject_id, rate=body.rate, seed=body.seed)
