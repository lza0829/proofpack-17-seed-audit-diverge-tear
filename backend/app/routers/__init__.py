from fastapi import APIRouter

from app.routers import audit, checklist, dashboard, packs, releases, samples, settings, subjects

api = APIRouter(prefix="/api")
api.include_router(dashboard.router)
api.include_router(subjects.router)
api.include_router(samples.router)
api.include_router(packs.router)
api.include_router(checklist.router)
api.include_router(releases.router)
api.include_router(settings.router)
api.include_router(audit.router)
