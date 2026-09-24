"""Execution profile and feature flags.

`AWS_AGENTIC_FEATURES_ENABLED=false` forces agentic cloud capabilities off and
selects local deterministic simulation.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ExecutionProfile = Literal["local", "aws-demo", "showcase"]

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    execution_profile: ExecutionProfile = "local"
    aws_agentic_features_enabled: bool = False
    cloud_integrations_enabled: bool = False
    agent_runtime_enabled: bool = False
    model_provider_enabled: bool = False
    agentic_features_enabled: bool = False
    multi_agent_execution_enabled: bool = False
    cloud_agent_runtime_enabled: bool = False
    managed_retrieval_enabled: bool = False
    long_term_memory_enabled: bool = False
    automatic_schedules_enabled: bool = False
    database_url: str = "postgresql+psycopg://opsmesh:opsmesh@localhost:5433/opsmesh"

    @property
    def effective_execution_profile(self) -> ExecutionProfile:
        if not self.aws_agentic_features_enabled:
            return "local"
        return self.execution_profile

    @property
    def local_deterministic_simulation(self) -> bool:
        return not self.aws_agentic_features_enabled

    def cloud_capability(self, requested: bool) -> bool:
        """Return whether a cloud capability is actually on.

        The master switch forces every agentic cloud capability off.
        """

        if not self.aws_agentic_features_enabled:
            return False
        return requested


@lru_cache
def get_settings() -> Settings:
    return Settings()
