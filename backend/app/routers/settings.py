from typing import Any

from fastapi import APIRouter, Body

from app.services import settings_service as svc

router = APIRouter(tags=["settings"])


@router.get("/settings")
def list_settings():
    return svc.list_settings()


@router.post("/settings")
def update_settings(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return svc.update_settings(payload)
