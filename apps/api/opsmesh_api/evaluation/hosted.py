"""Optional fixture sink. It does not score and it does not replace the local scorer."""

from collections.abc import Callable
from typing import Protocol

from opsmesh_api.evaluation.schema import GoldenWorkflow
from opsmesh_api.settings import Settings


class HostedEvaluationPort(Protocol):
    """Ingest fixtures. Callers keep the local report either way."""

    def ingest(self, workflows: list[GoldenWorkflow]) -> None:
        """Accept the fixtures that were just scored locally."""


class FixtureSink:
    """In-process sink used when the flag is on and no remote endpoint is configured."""

    def __init__(self) -> None:
        self.workflows: list[GoldenWorkflow] = []

    def ingest(self, workflows: list[GoldenWorkflow]) -> None:
        self.workflows.extend(workflows)


def ingest_if_enabled(
    settings: Settings,
    workflows: list[GoldenWorkflow],
    *,
    client_factory: Callable[[], HostedEvaluationPort] | None = None,
) -> bool:
    """Construct a client only when the hosted flag is actually on.

    The master switch forces the client off. A failure here does not change
    scores the local scorer already produced.
    """

    if not settings.cloud_capability(settings.hosted_evaluation_enabled):
        return False
    factory = client_factory if client_factory is not None else FixtureSink
    factory().ingest(workflows)
    return True
