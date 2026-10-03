"""Amazon Bedrock client behind the model port.

Local tests inject a fixture model. This client is used only when a live
provider is selected.
"""

import importlib
from typing import Any, Protocol, cast

from opsmesh_api.agents.ceilings import OUTPUT_TOKEN_CEILING
from opsmesh_api.agents.model_port import (
    ModelCompletion,
    ModelCompletionRequest,
    ModelProviderError,
)


class _ConverseClient(Protocol):
    def converse(self, **kwargs: Any) -> dict[str, Any]:
        """Invoke the provider converse operation."""


class BedrockModelClient:
    """Call Bedrock Converse with the request's token ceilings."""

    def __init__(self, region: str, client: _ConverseClient | None = None) -> None:
        self._region = region
        self._client = client

    def complete(self, request: ModelCompletionRequest) -> ModelCompletion:
        max_tokens = min(request.max_output_tokens, OUTPUT_TOKEN_CEILING)
        try:
            response = self._runtime().converse(
                modelId=request.model_id,
                system=[{"text": request.system_prompt}],
                messages=[{"role": "user", "content": [{"text": request.user_prompt}]}],
                inferenceConfig={"maxTokens": max_tokens},
            )
        except ModelProviderError:
            raise
        except Exception as exc:
            raise ModelProviderError("The model provider did not return a result.") from exc
        text, output_tokens = _completion_text(response)
        return ModelCompletion(
            text=text,
            output_tokens=output_tokens,
            model_id=request.model_id,
            prompt_version=request.prompt_version,
        )

    def _runtime(self) -> _ConverseClient:
        if self._client is not None:
            return self._client
        try:
            module = importlib.import_module("boto3")
        except ImportError as exc:
            raise ModelProviderError("The Bedrock client is not installed.") from exc
        factory = getattr(module, "client", None)
        if not callable(factory):
            raise ModelProviderError("The Bedrock client is not installed.")
        return cast(_ConverseClient, factory("bedrock-runtime", region_name=self._region))


def _completion_text(response: dict[str, Any]) -> tuple[str, int]:
    try:
        content = response["output"]["message"]["content"]
        text = content[0]["text"]
        output_tokens = int(response["usage"]["outputTokens"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ModelProviderError("The model provider returned an unexpected result.") from exc
    if not isinstance(text, str):
        raise ModelProviderError("The model provider returned an unexpected result.")
    return text, output_tokens
