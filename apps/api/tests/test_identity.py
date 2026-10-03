"""Tenant and role claims are enforced on routes, tools, and agent calls."""

import json
import os
import urllib.request
from time import time
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from opsmesh_api.agents.contracts import AgentTask, AgentType
from opsmesh_api.agents.runtime import task_payload
from opsmesh_api.db import get_db
from opsmesh_api.identity.cognito import CognitoTokenVerifier
from opsmesh_api.identity.issuer import LocalTokenIssuer
from opsmesh_api.identity.principal import Principal, normalize_platform_role
from opsmesh_api.identity.provider import verifier_for
from opsmesh_api.main import create_app
from opsmesh_api.settings import Settings, get_settings
from opsmesh_api.tools.gateway import GatewaySpecialistConfig, build_gateway_request
from opsmesh_api.tools.port import ToolCall
from opsmesh_api.tools.registry import LocalToolRegistry
from sqlalchemy.orm import Session, sessionmaker
from tests.auth_tokens import bearer
from tests.test_tool_registry import _call, _task

NORTHWIND = "northwind"
CONTOSO = "contoso"
REORDER = {
    "requested_by": "analyst@northwind.example",
    "sku": "ABC-42",
    "region": "mx-central",
    "service_level": 0.90,
}


def test_health_stays_open_and_business_routes_require_a_token(client: TestClient) -> None:
    health = client.get("/health", headers={"Authorization": ""})
    assert health.status_code == 200
    denied = client.get(f"/tenants/{NORTHWIND}/workflow-runs", headers={"Authorization": ""})
    assert denied.status_code == 401
    assert denied.json()["detail"]["code"] == "unauthenticated"


def test_missing_tenant_and_unknown_role_are_rejected(client: TestClient) -> None:
    issuer = LocalTokenIssuer(Settings(_env_file=None).local_identity_secret)
    missing_tenant = issuer.issue_claims(
        {"sub": "analyst@northwind.example", "cognito:groups": ["operations_analyst"]}
    )
    unknown_role = issuer.issue_claims(
        {
            "sub": "analyst@northwind.example",
            "tenant_id": NORTHWIND,
            "cognito:groups": ["wizard"],
        }
    )
    expired = issuer.issue(
        Principal(
            user_id="analyst@northwind.example",
            tenant_id=NORTHWIND,
            roles=frozenset({"operations_analyst"}),
        ),
        ttl_seconds=-30,
    )
    for token, code in (
        (missing_tenant, "missing_tenant"),
        (unknown_role, "unknown_role"),
        (expired, "invalid_token"),
    ):
        response = client.get(
            f"/tenants/{NORTHWIND}/finance/budget",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == code
        assert CONTOSO not in response.text


@pytest.mark.parametrize(
    "role",
    ["viewer", "procurement_manager", "finance_approver", "admin"],
)
def test_only_an_analyst_can_start_a_workflow(client: TestClient, role: str) -> None:
    response = client.post(
        f"/tenants/{NORTHWIND}/operations/reorder",
        headers=bearer(f"{role}@northwind.example", role),
        json=REORDER,
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "role_denied"
    assert "quantity" not in response.text
    assert "ABC-42" not in response.text


def test_cross_tenant_workflow_read_is_not_found(client: TestClient) -> None:
    started = client.post(f"/tenants/{NORTHWIND}/operations/reorder", json=REORDER)
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    hidden = client.get(f"/tenants/{CONTOSO}/workflow-runs/{run_id}")
    assert hidden.status_code == 404
    assert hidden.json() == {"detail": "Not found"}
    assert run_id not in hidden.text
    assert "ABC-42" not in hidden.text
    assert "northwind" not in hidden.text


def test_a_manager_cannot_approve_another_tenant(client: TestClient) -> None:
    started = client.post(f"/tenants/{NORTHWIND}/operations/reorder", json=REORDER)
    run_id = started.json()["run_id"]
    proposal = client.get(f"/tenants/{NORTHWIND}/workflow-runs/{run_id}").json()["proposals"][0]
    approval_id = proposal["approvals"][0]["id"]
    foreign = client.post(
        f"/tenants/{CONTOSO}/approvals/{approval_id}/decision",
        headers=bearer("manager@northwind.example", "procurement_manager"),
        json={"decision": "approve"},
    )
    assert foreign.status_code == 404
    assert foreign.json() == {"detail": "Not found"}
    assert approval_id not in foreign.text
    assert "approved" not in foreign.text.casefold()
    still = client.get(f"/tenants/{NORTHWIND}/approvals/{approval_id}")
    assert still.json()["status"] == "PENDING"


@pytest.mark.parametrize(
    "role",
    ["viewer", "operations_analyst", "finance_approver", "admin"],
)
def test_other_roles_cannot_take_the_manager_decision(client: TestClient, role: str) -> None:
    started = client.post(f"/tenants/{NORTHWIND}/operations/reorder", json=REORDER)
    run_id = started.json()["run_id"]
    approval_id = client.get(f"/tenants/{NORTHWIND}/workflow-runs/{run_id}").json()["proposals"][
        0
    ]["approvals"][0]["id"]
    denied = client.post(
        f"/tenants/{NORTHWIND}/approvals/{approval_id}/decision",
        headers=bearer(f"{role}@northwind.example", role),
        json={"decision": "approve"},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "role_denied"


def test_tenant_admin_cannot_read_another_tenant(client: TestClient) -> None:
    response = client.get(
        f"/tenants/{CONTOSO}/finance/budget",
        headers=bearer("admin@northwind.example", "admin"),
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
    assert "80000" not in response.text
    assert "4200000" not in response.text
    assert normalize_platform_role("admin") == ""


def test_a_configured_platform_role_can_read_but_not_decide(
    session_factory: sessionmaker[Session],
) -> None:
    settings = Settings(_env_file=None, platform_role="platform_operator")
    token = bearer(
        "ops@example.com",
        "viewer",
        NORTHWIND,
        platform=True,
        platform_role="platform_operator",
    )

    def override_db() -> object:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as platform_client:
        budget = platform_client.get(f"/tenants/{CONTOSO}/finance/budget", headers=token)
        assert budget.status_code == 200
        assert budget.json()["available_cents"] == 80_000
        denied = platform_client.post(
            f"/tenants/{CONTOSO}/operations/reorder",
            headers=token,
            json=REORDER,
        )
        assert denied.status_code == 404
        assert denied.json() == {"detail": "Not found"}


def test_local_profile_does_not_call_a_hosted_issuer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("hosted identity was contacted")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    assert verifier_for(Settings(_env_file=None)).__class__.__name__ == "LocalTokenIssuer"
    response = client.get(f"/tenants/{NORTHWIND}/finance/budget")
    assert response.status_code == 200
    session = client.post(
        "/auth/session",
        json={"tenant_id": CONTOSO, "role": "operations_analyst"},
    )
    assert session.status_code == 200
    body = session.json()
    assert body["tenant_id"] == CONTOSO
    assert body["roles"] == ["operations_analyst"]
    assert body["user_id"] == "analyst@contoso.example"
    scoped = client.get(
        f"/tenants/{CONTOSO}/finance/budget",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert scoped.status_code == 200
    assert scoped.json()["available_cents"] == 80_000


def test_hosted_profile_does_not_issue_local_sessions(
    session_factory: sessionmaker[Session],
) -> None:
    settings = Settings(
        _env_file=None,
        execution_profile="aws-demo",
        aws_agentic_features_enabled=True,
    )

    def override_db() -> object:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as hosted:
        opened = hosted.post(
            "/auth/session",
            json={"tenant_id": NORTHWIND, "role": "operations_analyst"},
        )
        assert opened.status_code == 404
        rejected = hosted.get(
            f"/tenants/{NORTHWIND}/finance/budget",
            headers=bearer("analyst@northwind.example", "operations_analyst"),
        )
        assert rejected.status_code == 401
        assert rejected.json()["detail"]["code"] == "invalid_token"


def test_cognito_claims_map_without_calling_the_user_pool() -> None:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_pool"

    class _Keys:
        def get_key(self, kid: str) -> object:
            assert kid == "test-key"
            return private.public_key()

    token = jwt.encode(
        {
            "sub": "analyst@northwind.example",
            "custom:tenant_id": NORTHWIND,
            "cognito:groups": ["operations_analyst"],
            "iss": issuer,
            "token_use": "access",
            "client_id": "client",
            "exp": int(time()) + 600,
            "iat": int(time()),
        },
        private,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    verifier = CognitoTokenVerifier(
        region="us-east-1",
        user_pool_id="us-east-1_pool",
        client_id="client",
        keys=_Keys(),
    )
    principal = verifier.verify(token)
    assert principal.user_id == "analyst@northwind.example"
    assert principal.tenant_id == NORTHWIND
    assert principal.roles == frozenset({"operations_analyst"})
    assert principal.platform is False

    missing = jwt.encode(
        {
            "sub": "analyst@northwind.example",
            "cognito:groups": ["operations_analyst"],
            "iss": issuer,
            "token_use": "access",
            "client_id": "client",
            "exp": int(time()) + 600,
        },
        private,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    with pytest.raises(Exception) as caught:
        verifier.verify(missing)
    assert getattr(caught.value, "code", "") == "missing_tenant"


def test_tool_call_for_another_tenant_is_denied(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        registry = LocalToolRegistry(db)
        run_id, task_id = _task(db, AgentType.FINANCE)
        named = registry.invoke(
            _call(
                run_id,
                task_id,
                AgentType.FINANCE,
                "finance.get_budget",
                {"tenant_id": CONTOSO},
            )
        )
        assert named.status.value == "FAILED"
        assert named.error_code == "denied"
        assert named.data is None
        assert "80000" not in json.dumps(named.model_dump(mode="json"))
        assert "4200000" not in json.dumps(named.model_dump(mode="json"))

        supplied = ToolCall(
            tool_name="finance.get_budget",
            agent=AgentType.FINANCE,
            tenant_id=CONTOSO,
            workflow_run_id=run_id,
            task_id=task_id,
            arguments={},
        )
        moved = registry.invoke(supplied)
        assert moved.status.value == "FAILED"
        assert moved.error_code == "denied"
        assert moved.data is None
        assert registry.handler_calls == 0


def test_agent_identity_is_not_the_end_user_token() -> None:
    user_token = "user-token-value"
    task = AgentTask(
        task_id=uuid4(),
        agent=AgentType.INVENTORY,
        tenant_id=NORTHWIND,
        parameters={"sku": "ABC-42", "tenant_id": CONTOSO, "access_token": user_token},
    )
    payload = task_payload(task, user_token)
    assert payload["tenant_id"] == NORTHWIND
    assert "tenant_id" not in payload["parameters"]
    assert user_token not in json.dumps(payload)
    config = GatewaySpecialistConfig(
        identities={AgentType.INVENTORY: user_token},
    )
    request = build_gateway_request(
        ToolCall(
            tool_name="inventory.get_snapshot",
            agent=AgentType.INVENTORY,
            tenant_id=NORTHWIND,
            workflow_run_id=uuid4(),
            task_id=task.task_id,
            arguments={"sku": "ABC-42", "region": "mx-central"},
        ),
        config,
        database_url="",
        end_user_token=user_token,
    )
    assert request.identity == "opsmesh/inventory"
    assert request.tenant_id == NORTHWIND
    assert user_token not in request.credential


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("OPSMESH_LIVE_COGNITO") != "1",
    reason="Hosted identity is not configured.",
)
def test_hosted_cognito_key_set_is_reachable() -> None:
    pool = os.environ["COGNITO_USER_POOL_ID"]
    region = os.environ.get("COGNITO_REGION", "us-east-1")
    url = f"https://cognito-idp.{region}.amazonaws.com/{pool}/.well-known/jwks.json"
    with urllib.request.urlopen(url, timeout=10) as response:
        body = json.load(response)
    assert body["keys"]
