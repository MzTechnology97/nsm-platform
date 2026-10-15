from fastapi import APIRouter
from sqlalchemy import func, select

from app import main as core
from app.db import SessionLocal
from app.demo import clear as clear_demo_data, seed as seed_demo_data
from app.models import Customer, User

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


def install_demo_ui(app):
    app.include_router(router)
