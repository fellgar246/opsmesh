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
    # Blank selects every specialist, in conversion order, when the provider is on.
    model_backed_agents: str = ""
    bedrock_model_id: str = "anthropic.claude-3-haiku-20240307-v1:0"
    bedrock_region: str = "us-east-1"
    agentic_features_enabled: bool = False
    multi_agent_execution_enabled: bool = False
    cloud_agent_runtime_enabled: bool = False
    # Blank selects no remote specialist. Names follow the conversion order.
    remote_runtime_agents: str = ""
    agent_runtime_endpoints: str = ""
    agent_runtime_identities: str = ""
    agent_runtime_qualifier: str = "DEFAULT"
    cloud_tool_gateway_enabled: bool = False
    tool_gateway_endpoint: str = ""
    # agent=identity pairs. Empty uses opsmesh/<agent> for each specialist.
    tool_gateway_identities: str = ""
    tool_gateway_protocol_version: str = "2026-07-28"
    cloud_policy_enabled: bool = False
    policy_engine_id: str = ""
    cloud_event_bus_enabled: bool = False
    event_bus_name: str = ""
    event_queue_url: str = ""
    managed_retrieval_enabled: bool = False
    long_term_memory_enabled: bool = False
    automatic_schedules_enabled: bool = False
    database_url: str = "postgresql+psycopg://opsmesh:opsmesh@localhost:5433/opsmesh"
    cognito_user_pool_id: str = ""
    cognito_region: str = "us-east-1"
    cognito_client_id: str = ""
    # Empty keeps tenant admin inside one tenant. A separate name is cross-tenant.
    platform_role: str = ""
    local_identity_secret: str = "opsmesh-local-development-identity"

    @property
    def effective_execution_profile(self) -> ExecutionProfile:
        if not self.aws_agentic_features_enabled:
            return "local"
        return self.execution_profile

    @property
    def local_deterministic_simulation(self) -> bool:
        return not self.aws_agentic_features_enabled

    @property
    def uses_local_identity(self) -> bool:
        """The local profile signs tokens itself. Other profiles use Cognito."""

        return self.effective_execution_profile == "local"

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
