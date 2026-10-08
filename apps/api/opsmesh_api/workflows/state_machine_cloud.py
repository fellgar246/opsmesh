"""Step Functions client for the approval wait.

Construct this only when the managed state machine is on. The client starts
an execution. It does not plan work and it does not call a model.
"""

import importlib
import json
from datetime import datetime
from typing import Any, Protocol, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh_api.settings import Settings
from opsmesh_api.workflows.approval_machine import (
    ApprovalProcessError,
    LocalApprovalProcess,
    ProcessSignal,
)
from opsmesh_api.workflows.models import ApprovalProcess


class _StepFunctionsClient(Protocol):
    def start_execution(self, **kwargs: Any) -> dict[str, Any]:
        """Start one state-machine execution."""


class StepFunctionsApprovalProcess:
    """Managed wait. A second start for the same proposal does not start another execution."""

    def __init__(
        self,
        db: Session,
        settings: Settings,
        client: _StepFunctionsClient | None = None,
    ) -> None:
        self._db = db
        self._settings = settings
        self._client = client
        self._local = LocalApprovalProcess(db)

    def start(self, tenant_id: str, proposal_id: UUID) -> ApprovalProcess:
        if not self._settings.state_machine_arn:
            raise ApprovalProcessError("The state machine is not configured.")
        existing = self._db.scalar(
            select(ApprovalProcess.id).where(
                ApprovalProcess.tenant_id == tenant_id,
                ApprovalProcess.proposal_id == proposal_id,
            )
        )
        row = self._local.start(tenant_id, proposal_id)
        if existing is None:
            self._start_execution(row)
        return row

    def signal(
        self,
        tenant_id: str,
        proposal_id: UUID,
        signal: ProcessSignal,
        now: datetime | None = None,
    ) -> ApprovalProcess:
        """Apply the transition through the approval and draft services."""

        return self._local.signal(tenant_id, proposal_id, signal, now)

    def _start_execution(self, row: ApprovalProcess) -> None:
        payload = {
            "proposal_id": str(row.proposal_id),
            "tenant_id": row.tenant_id,
            "arguments_hash": row.arguments_hash or "",
            "signal": "wait",
        }
        try:
            self._functions().start_execution(
                stateMachineArn=self._settings.state_machine_arn,
                name=f"approval-{row.proposal_id}",
                input=json.dumps(payload),
            )
        except ApprovalProcessError:
            raise
        except Exception as exc:
            raise ApprovalProcessError("The state machine did not start.") from exc

    def _functions(self) -> _StepFunctionsClient:
        if self._client is not None:
            return self._client
        try:
            module = importlib.import_module("boto3")
        except ImportError as exc:
            raise ApprovalProcessError("The state machine client is not installed.") from exc
        factory = getattr(module, "client", None)
        if not callable(factory):
            raise ApprovalProcessError("The state machine client is not installed.")
        return cast(
            _StepFunctionsClient,
            factory("stepfunctions", region_name=self._settings.bedrock_region),
        )
