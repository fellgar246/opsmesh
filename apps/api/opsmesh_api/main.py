"""HTTP API."""

from fastapi import FastAPI

from opsmesh_api.logging import configure_logging
from opsmesh_api.routes.finance import router as finance_router
from opsmesh_api.routes.forecast import router as forecast_router
from opsmesh_api.routes.health import router as health_router
from opsmesh_api.routes.inventory import router as inventory_router
from opsmesh_api.routes.procurement import router as procurement_router
from opsmesh_api.routes.supplier import router as supplier_router


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="OpsMesh API")
    app.include_router(health_router)
    app.include_router(inventory_router)
    app.include_router(forecast_router)
    app.include_router(supplier_router)
    app.include_router(finance_router)
    app.include_router(procurement_router)
    return app


app = create_app()
