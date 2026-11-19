from fastapi import APIRouter

from app.api.v1.endpoints import asr

api_router = APIRouter()
api_router.include_router(asr.router, tags=["asr"])
