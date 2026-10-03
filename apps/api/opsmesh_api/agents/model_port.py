"""Model port. Vendor clients implement `complete` and stay outside the coordinator."""

from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from opsmesh_api.agents.ceilings import INPUT_TOKEN_CEILING, OUTPUT_TOKEN_CEILING, estimate_tokens
from opsmesh_api.agents.contracts import AgentType


class ModelProviderError(Exception):
    """The model provider failed. Callers must not invent operational facts."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class ModelCompletionRequest(BaseModel):
    """One bounded model call for a single specialist task."""

    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=1, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=64)
    system_prompt: str
    user_prompt: str
    max_input_tokens: int = Field(ge=0, le=INPUT_TOKEN_CEILING)
    max_output_tokens: int = Field(ge=1, le=OUTPUT_TOKEN_CEILING)
    permitted_tools: tuple[str, ...]
    task_id: UUID
    agent: AgentType

    @model_validator(mode="after")
    def input_stays_within_ceiling(self) -> "ModelCompletionRequest":
        total = estimate_tokens(self.system_prompt) + estimate_tokens(self.user_prompt)
        if total > INPUT_TOKEN_CEILING:
            raise ValueError("The model input exceeds the input token ceiling.")
        return self


class ModelCompletion(BaseModel):
    """Raw model output. The adapter parses it into the result envelope."""

    model_config = ConfigDict(extra="forbid")

    text: str
    output_tokens: int = Field(ge=0)
    model_id: str = Field(min_length=1, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=64)


class ModelPort(Protocol):
    """Replaceable model provider. The coordinator never calls this directly."""

    def complete(self, request: ModelCompletionRequest) -> ModelCompletion:
        """Return one completion. Implementations must honor the request ceilings."""
