from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app import main as core
from app.db import SessionLocal
from app.models import Site

router = APIRouter()


@router.get("/sites", response_class=HTMLResponse, name="sites_index")
def sites_index(request: Request, q: str = ""):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "customers.read"):
            raise HTTPException(403)
        stmt = select(Site).options(selectinload(Site.customer), selectinload(Site.devices))
        term = q.strip()
        if term:
            like = f"%{term}%"
            stmt = stmt.where(or_(Site.name.ilike(like), Site.address.ilike(like)))
        sites = list(db.scalars(stmt.order_by(Site.name)))
        return core.render(request, db, user, "sites.html", sites=sites, q=term)


def install_sites_ui(app):
    app.include_router(router)
