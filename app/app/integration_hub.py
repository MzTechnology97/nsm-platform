from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app import main as core
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration

router = APIRouter()


@router.get("/admin/integrations", response_class=HTMLResponse, name="admin_integrations")
def admin_integrations(request: Request):
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        rows = list(db.scalars(select(ConnectorIntegration).order_by(ConnectorIntegration.provider)))
        integrations = {row.provider: row for row in rows}
        return core.render(
            request,
            db,
            user,
            "admin_integrations.html",
            integrations=integrations,
        )


def install_integration_hub(app):
    app.include_router(router)
