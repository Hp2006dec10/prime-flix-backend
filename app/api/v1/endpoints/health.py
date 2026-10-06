from fastapi import APIRouter
from pydantic import BaseModel
from datetime import datetime
from app.core.config import settings

router = APIRouter()


class HealthCheckResponse(BaseModel):
    status: str
    app_name: str
    version: str
    timestamp: datetime


@router.get("/health", response_model=HealthCheckResponse, summary="Health Check Endpoint")
async def health_check():
    """
    Returns the service health status, application name, version, and current UTC server timestamp.
    """
    return HealthCheckResponse(
        status="ok",
        app_name=settings.PROJECT_NAME,
        version=settings.VERSION,
        timestamp=datetime.utcnow()
    )
