from fastapi import APIRouter
from app.api.v1.health import router as health_router
from app.api.v1.providers import router as providers_router
from app.api.v1.datasets import router as datasets_router

api_router = APIRouter()

api_router.include_router(health_router)
api_router.include_router(providers_router)
api_router.include_router(datasets_router)
