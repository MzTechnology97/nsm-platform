from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from app import main as core
from app.db import SessionLocal
from app.demo import clear as clear_demo_data, seed as seed_demo_data
from app.models import Customer
from app.security import validate_csrf

router = APIRouter()


@router.get("/admin/demo", response_class=HTMLResponse, name="admin_demo")
def admin_demo(request: Request):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "users.manage"):
            raise HTTPException(403)
        demo_customers = db.scalar(select(func.count(Customer.id)).where(Customer.code.like("DEMO-%"))) or 0
        return core.render(
            request,
            db,
            user,
            "admin_demo.html",
            demo_customers=demo_customers,
            status=request.query_params.get("status", ""),
        )


@router.post("/admin/demo/seed")
async def admin_demo_seed(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf", "")))
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        actor_id = user.id
    seed_demo_data()
    with SessionLocal() as db:
        actor = db.get(core.User, actor_id)
        core.add_event(db, "DEMO_DATASET_SEEDED", actor=actor, details={"source": "admin_ui"})
        db.commit()
    return RedirectResponse("/admin/demo?status=seeded", status_code=303)


@router.post("/admin/demo/clear")
async def admin_demo_clear(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf", "")))
    confirm = str(form.get("confirm", "")).strip().upper()
    if confirm != "DEMO":
        raise HTTPException(400, "Digita DEMO per confermare.")
    with SessionLocal() as db:
        user = core.require_admin(request, db)
        actor_id = user.id
    clear_demo_data()
    with SessionLocal() as db:
        actor = db.get(core.User, actor_id)
        core.add_event(db, "DEMO_DATASET_CLEARED", actor=actor, details={"source": "admin_ui"})
        db.commit()
    return RedirectResponse("/admin/demo?status=cleared", status_code=303)


def install_demo_ui(app, templates):
    app.include_router(router)
