"""HTTP API."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from opsmesh_api.logging import configure_logging
from opsmesh_api.routes.finance import router as finance_router
from opsmesh_api.routes.forecast import router as forecast_router
from opsmesh_api.routes.health import router as health_router
from opsmesh_api.routes.inventory import router as inventory_router
from opsmesh_api.routes.operations import router as operations_router
from opsmesh_api.routes.procurement import router as procurement_router
from opsmesh_api.routes.supplier import router as supplier_router
from opsmesh_api.routes.tenants import router as tenant_router
from opsmesh_api.routes.workflows import router as workflow_router


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="OpsMesh API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(tenant_router)
    app.include_router(inventory_router)
    app.include_router(forecast_router)
    app.include_router(supplier_router)
    app.include_router(finance_router)
    app.include_router(procurement_router)
    app.include_router(workflow_router)
    app.include_router(operations_router)
    return app


app = create_app()
