"""Forecast provider port: local fixtures by default, DemandPilot behind the adapter."""

import ast
import json
import socket
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from opsmesh_api.agents.stubs import StubAgentClient
from opsmesh_api.forecasts.demandpilot import (
    PROVIDER_DEMANDPILOT,
    DemandPilotProvider,
    map_demandpilot_document,
)
from opsmesh_api.forecasts.fixture import FixtureForecastProvider
from opsmesh_api.forecasts.port import PROVIDER_FIXTURE, ForecastUnavailable, ProviderConfig
from opsmesh_api.forecasts.select import select_forecast_provider
from opsmesh_api.operations.engine import OperationalEngine
from opsmesh_api.operations.quantity import solve_reorder
from opsmesh_api.operations.schemas import ReorderRead, ReorderRequest
from opsmesh_api.settings import Settings
from opsmesh_api.workflows.models import WorkflowStatus
from opsmesh_api.workflows.store import WorkflowStore
from sqlalchemy.orm import Session, sessionmaker

NORTHWIND = "northwind"
CONTOSO = "contoso"
SKU = "ABC-42"
REGION = "mx-central"
HORIZON = 30
REQUEST = ReorderRequest(
    requested_by="analyst@northwind.example",
    sku=SKU,
    region=REGION,
    service_level=0.90,
)


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class _ForecastServer:
    def __init__(self) -> None:
        self.payload: dict[str, object] = _document()
        self.status = 200
        self.received: list[tuple[str, str | None]] = []
        self._httpd: ThreadingHTTPServer | None = None

    def start(self) -> str:
        state = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                state.received.append((self.path, self.headers.get("Authorization")))
                body = b"" if state.status != 200 else json.dumps(state.payload).encode()
                self.send_response(state.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self._httpd = httpd
        host, port = httpd.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()


def _document(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "forecastId": "fc-1001",
        "sku": SKU,
        "region": REGION,
        "horizon": {"days": HORIZON},
        "bands": {"p10": 280, "p50": 330, "p90": 400},
        "qualityScore": 0.5,
        "generatedAt": "2026-10-01T15:00:00Z",
    }
    body.update(overrides)
    return body


def _provider(
    base_url: str,
    *,
    freshness: float | None = None,
    clock: _Clock | None = None,
    tenants: dict[str, str] | None = None,
) -> DemandPilotProvider:
    return DemandPilotProvider(
        ProviderConfig(
            base_url=base_url,
            tenant_mapping=tenants
            if tenants is not None
            else {NORTHWIND: "dp-north", CONTOSO: "dp-contoso"},
            timeout_seconds=2,
            freshness_seconds=freshness,
        ),
        api_token="secret-token",
        clock=clock,
    )


def _reorder(db: Session, provider: DemandPilotProvider) -> ReorderRead:
    engine = OperationalEngine(
        db,
        agent_client=StubAgentClient(db, forecast_provider=provider),
        forecast_provider=provider,
    )
    return engine.reorder(NORTHWIND, REQUEST)


def _forecast_evidence(db: Session, run_id: object) -> list[tuple[str, str]]:
    from uuid import UUID

    stored = WorkflowStore(db).get_run(NORTHWIND, UUID(str(run_id)))
    return [
        (item.source, item.source_record_id) for item in stored.evidence if item.type == "forecast"
    ]


def test_external_provider_is_off_by_default(session_factory: sessionmaker[Session]) -> None:
    settings = Settings(_env_file=None)
    assert settings.forecast_provider == "fixture"
    assert settings.demandpilot_base_url == ""
    assert settings.demandpilot_freshness_seconds == ""
    with session_factory() as db:
        provider = select_forecast_provider(db, settings)
        reading = provider.read(NORTHWIND, SKU, REGION, HORIZON)
    assert isinstance(provider, FixtureForecastProvider)
    assert reading.provider == PROVIDER_FIXTURE
    assert reading.p90 == 345


def test_fixture_reorder_keeps_the_golden_quantity(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        reading = FixtureForecastProvider(db).read(NORTHWIND, SKU, REGION, HORIZON)
        assert reading.p10 == 275
        assert reading.p50 == 310
        assert reading.p90 == 345
        assert reading.quality == 0.75
        assert reading.horizon_days == HORIZON
        assert reading.observed_at.tzinfo is not None
        assert reading.source_record_id == f"{SKU}:{REGION}:{HORIZON}"
        result = OperationalEngine(db).reorder(NORTHWIND, REQUEST)
        evidence = _forecast_evidence(db, result.run_id)
    assert result.selected_demand == 345
    assert result.quantity == 250
    assert evidence == [(PROVIDER_FIXTURE, f"{SKU}:{REGION}:{HORIZON}")]


def test_mapper_translates_a_demandpilot_document() -> None:
    reading = map_demandpilot_document(
        _document(),
        tenant_id=NORTHWIND,
        sku=SKU,
        region=REGION,
        horizon_days=HORIZON,
    )
    assert reading.p10 == 280
    assert reading.p50 == 330
    assert reading.p90 == 400
    assert reading.quality == 0.5
    assert reading.horizon_days == HORIZON
    assert reading.observed_at == datetime(2026, 10, 1, 15, tzinfo=UTC)
    assert reading.provider == PROVIDER_DEMANDPILOT
    assert reading.source_record_id == "fc-1001"


def test_mapper_rejects_a_document_missing_p90() -> None:
    document = _document(bands={"p10": 280, "p50": 330})
    with pytest.raises(ForecastUnavailable, match="P90"):
        map_demandpilot_document(
            document,
            tenant_id=NORTHWIND,
            sku=SKU,
            region=REGION,
            horizon_days=HORIZON,
        )


def test_fake_server_changes_p90_and_the_reorder_quantity(
    session_factory: sessionmaker[Session],
) -> None:
    server = _ForecastServer()
    base_url = server.start()
    try:
        provider = _provider(base_url)
        with session_factory() as db:
            result = _reorder(db, provider)
            evidence = _forecast_evidence(db, result.run_id)
    finally:
        server.close()
    expected = solve_reorder(
        service_level=0.90,
        p50=330,
        p90=400,
        available_units=128,
        case_pack=50,
        minimum_order_quantity=100,
        unit_cost_cents=3700,
        supplier_status="approved",
        available_cents=4_200_000,
    )
    assert result.selected_demand == 400
    assert result.quantity == expected.quantity
    assert result.quantity != 250
    assert evidence == [(PROVIDER_DEMANDPILOT, "fc-1001")]
    path, authorization = server.received[0]
    assert path.startswith("/v1/forecasts/dp-north/ABC-42/mx-central")
    assert "horizonDays=30" in path
    assert "northwind" not in path
    assert authorization == "Bearer secret-token"


def test_missing_p90_ends_the_reorder_as_missing_evidence(
    session_factory: sessionmaker[Session],
) -> None:
    server = _ForecastServer()
    server.payload = _document(bands={"p10": 280, "p50": 330})
    base_url = server.start()
    try:
        with session_factory() as db:
            result = _reorder(db, _provider(base_url))
    finally:
        server.close()
    assert result.status == WorkflowStatus.FAILED
    assert result.reason == "missing_evidence"
    assert result.selected_demand is None
    assert result.quantity is None


def test_provider_down_fails_closed_without_the_fixture(
    session_factory: sessionmaker[Session],
) -> None:
    server = _ForecastServer()
    server.status = 503
    base_url = server.start()
    try:
        with session_factory() as db:
            result = _reorder(db, _provider(base_url))
    finally:
        server.close()
    assert result.status == WorkflowStatus.FAILED
    assert result.reason == "missing_evidence"
    assert result.selected_demand is None
    assert result.quantity is None


def test_closed_port_fails_closed() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    provider = _provider(f"http://127.0.0.1:{port}")
    with pytest.raises(ForecastUnavailable, match="did not respond"):
        provider.read(NORTHWIND, SKU, REGION, HORIZON)


def test_cache_is_used_only_inside_an_explicit_freshness_window(
    session_factory: sessionmaker[Session],
) -> None:
    clock = _Clock()
    server = _ForecastServer()
    base_url = server.start()
    provider = _provider(base_url, freshness=60, clock=clock)
    try:
        with session_factory() as db:
            fresh = _reorder(db, provider)
            server.status = 503
            clock.advance(30)
            cached = _reorder(db, provider)
            cached_evidence = _forecast_evidence(db, cached.run_id)
            clock.advance(31)
            expired = _reorder(db, provider)
    finally:
        server.close()
    assert fresh.quantity == cached.quantity
    assert cached.selected_demand == 400
    assert cached_evidence == [(PROVIDER_DEMANDPILOT, "fc-1001")]
    assert expired.reason == "missing_evidence"
    assert expired.selected_demand is None


def test_a_bad_document_does_not_reuse_the_cache() -> None:
    clock = _Clock()
    server = _ForecastServer()
    base_url = server.start()
    provider = _provider(base_url, freshness=60, clock=clock)
    try:
        first = provider.read(NORTHWIND, SKU, REGION, HORIZON)
        server.payload = _document(bands={"p10": 280, "p50": 330})
        with pytest.raises(ForecastUnavailable, match="P90"):
            provider.read(NORTHWIND, SKU, REGION, HORIZON)
    finally:
        server.close()
    assert first.p90 == 400


def test_cache_does_not_cross_tenants() -> None:
    server = _ForecastServer()
    base_url = server.start()
    provider = _provider(base_url, freshness=60)
    try:
        north = provider.read(NORTHWIND, SKU, REGION, HORIZON)
        server.status = 503
        with pytest.raises(ForecastUnavailable):
            provider.read(CONTOSO, SKU, REGION, HORIZON)
        again = provider.read(NORTHWIND, SKU, REGION, HORIZON)
    finally:
        server.close()
    assert north.p90 == 400
    assert again.p90 == 400
    assert again.tenant_id == NORTHWIND


def test_unmapped_tenant_is_not_sent_as_itself() -> None:
    server = _ForecastServer()
    base_url = server.start()
    provider = _provider(base_url, tenants={NORTHWIND: "dp-north"})
    try:
        with pytest.raises(ForecastUnavailable, match="not mapped"):
            provider.read(CONTOSO, SKU, REGION, HORIZON)
    finally:
        server.close()
    assert server.received == []


def test_workflow_packages_do_not_import_the_external_client() -> None:
    root = Path(__file__).resolve().parents[1] / "opsmesh_api"
    offenders: list[str] = []
    for package in ("operations", "workflows", "agents"):
        for path in (root / package).rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                module = ""
                if isinstance(node, ast.Import):
                    module = " ".join(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    module = node.module
                if "forecasts.demandpilot" in module:
                    offenders.append(str(path))
    assert offenders == []
