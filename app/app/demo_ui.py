from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from app import main as core
from app.db import SessionLocal
from app.demo import clear as clear_demo_data, seed as seed_demo_data
from app.models import Customer, User
from app.security import validate_csrf

router = APIRouter()


def demo_customers(db) -> int:
    return int(db.scalar(select(func.count(Customer.id)).where(Customer.code.like("DEMO-%"))) or 0)


def run_seed(actor_id) -> None:
    """Create the demo dataset and audit it as the given administrator."""
    seed_demo_data()
    with SessionLocal() as db:
        core.add_event(db, "DEMO_DATASET_SEEDED", actor=db.get(User, actor_id), details={"source": "admin_ui"})
        db.commit()


def run_clear(actor_id) -> None:
    """Remove only demo objects and demo backup files, audited as the given administrator."""
    clear_demo_data()
    with SessionLocal() as db:
        core.add_event(db, "DEMO_DATASET_CLEARED", actor=db.get(User, actor_id), details={"source": "admin_ui"})
        db.commit()


@router.get("/admin/demo", response_class=HTMLResponse, name="admin_demo")
def admin_demo(request: Request):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "users.manage"):
            raise HTTPException(403)
        return core.render(
            request,
            db,
            user,
            "admin_demo.html",
            demo_customers=demo_customers(db),
            status=request.query_params.get("status", ""),
        )


@router.post("/admin/demo/seed")
async def admin_demo_seed(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf", "")))
    with SessionLocal() as db:
        actor = core.require_admin(request, db)
        actor_id = actor.id
    run_seed(actor_id)
    return RedirectResponse("/admin/demo?status=seeded", status_code=303)


@router.post("/admin/demo/clear")
async def admin_demo_clear(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf", "")))
    if str(form.get("confirm", "")).strip().upper() != "DEMO":
        raise HTTPException(400, "Digita DEMO per confermare.")
    with SessionLocal() as db:
        actor = core.require_admin(request, db)
        actor_id = actor.id
    run_clear(actor_id)
    return RedirectResponse("/admin/demo?status=cleared", status_code=303)


def install_demo_ui(app):
    app.include_router(router)
