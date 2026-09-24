"""Health route."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from opsmesh_api.db import get_db
from opsmesh_api.logging import get_logger
from opsmesh_api.settings import ExecutionProfile, Settings, get_settings

router = APIRouter()
logger = get_logger(__name__)


class HealthResponse(BaseModel):
    status: str
    execution_profile: ExecutionProfile
    local_deterministic_simulation: bool
    cloud_integrations_enabled: bool
    agent_runtime_enabled: bool
    model_provider_enabled: bool


def _health_payload(settings: Settings) -> HealthResponse:
    return HealthResponse(
        status="ok",
        execution_profile=settings.effective_execution_profile,
        local_deterministic_simulation=settings.local_deterministic_simulation,
        cloud_integrations_enabled=settings.cloud_capability(settings.cloud_integrations_enabled),
        agent_runtime_enabled=settings.cloud_capability(settings.agent_runtime_enabled),
        model_provider_enabled=settings.cloud_capability(settings.model_provider_enabled),
    )


@router.get("/health", response_model=HealthResponse)
def health(
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
) -> HealthResponse:
    db.execute(text("SELECT 1"))
    payload = _health_payload(settings)
    logger.info("health_checked", execution_profile=payload.execution_profile)
    return payload
