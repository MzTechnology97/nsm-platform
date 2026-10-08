"""Delegated administration: users limited to a set of customers.

An administrator assigns customers to a non-admin user (*Amministrazione →
Utenti → Clienti visibili*).  While that user is served, every ORM query of the
request is filtered centrally (SQLAlchemy ``do_orm_execute`` +
``with_loader_criteria``), so pages, lists, counters, search, reports and
APIs only see:

- the assigned customers;
- rows with a ``customer_id`` of those customers (devices, sites, incidents,
  issues, notifications, audit events, reports, …);
- rows with a ``device_id`` of a device of those customers (backups, jobs,
  telemetry, logs, …).

A detail URL of another customer's object therefore answers 404.  The filter
is bound to the request (a context variable set when the session user is
resolved): the worker and agent endpoints are never filtered.  Administrators
are never limited.  A customer created by a limited user is added to the
user's scope, so delegated onboarding keeps working.
"""
from __future__ import annotations

import uuid
from contextvars import ContextVar

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import event, select
from sqlalchemy.orm import Session, with_loader_criteria

from app import main as core
from app.db import Base, SessionLocal
from app.models import Customer, Device, User
from app.security import validate_csrf

router = APIRouter()

_scope: ContextVar[frozenset | None] = ContextVar("nsm_customer_scope", default=None)
_scope_user: ContextVar[uuid.UUID | None] = ContextVar("nsm_customer_scope_user", default=None)
_bypass: ContextVar[bool] = ContextVar("nsm_customer_scope_bypass", default=False)
_MODELS: dict = {}


def user_scope(user) -> frozenset | None:
    """Customer ids visible to the user, or None when unrestricted."""
    if user is None or getattr(user, "role", None) == "admin":
        return None
    ids = set()
    for value in getattr(user, "customer_scope", None) or []:
        try:
            ids.add(uuid.UUID(str(value)))
        except ValueError:
            continue
    return frozenset(ids) if ids else None


def current_scope() -> frozenset | None:
    return _scope.get()


def activate(user) -> None:
    scope = user_scope(user)
    _scope.set(scope)
    _scope_user.set(user.id if scope is not None and user is not None else None)


def _discover_models() -> None:
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        columns = set(mapper.columns.keys())
        if cls is Customer:
            _MODELS[cls] = "customer"
        elif "customer_id" in columns:
            _MODELS[cls] = "customer_id"
        elif "device_id" in columns and cls is not Device:
            _MODELS[cls] = "device_id"


def _criteria(cls, kind: str, ids: list):
    if kind == "customer":
        return cls.id.in_(ids)
    if kind == "customer_id":
        return cls.customer_id.in_(ids)
    return cls.device_id.in_(select(Device.id).where(Device.customer_id.in_(ids)))


def _filter(execute_state):
    scope = _scope.get()
    if scope is None or _bypass.get() or not execute_state.is_select:
        return
    ids = sorted(scope)
    options = [with_loader_criteria(cls, _criteria(cls, kind, ids), include_aliases=True) for cls, kind in _MODELS.items()]
    execute_state.statement = execute_state.statement.options(*options)


def _adopt_new_customers(session, flush_context, instances):
    """A customer created by a limited user joins that user's scope."""
    user_id = _scope_user.get()
    scope = _scope.get()
    if user_id is None or scope is None:
        return
    created = [obj for obj in session.new if isinstance(obj, Customer)]
    if not created:
        return
    for customer in created:
        if customer.id is None:
            customer.id = uuid.uuid4()
    token = _bypass.set(True)
    try:
        user = session.get(User, user_id)
    finally:
        _bypass.reset(token)
    if user is None:
        return
    ids = list(user.customer_scope or []) + [str(c.id) for c in created]
    user.customer_scope = ids
    _scope.set(frozenset(scope | {c.id for c in created}))


def _wrap(resolver):
    def resolve(request, db):
        user = resolver(request, db)
        activate(user)
        return user

    return resolve


@router.post("/admin/users/{user_id}/scope", name="admin_user_scope")
async def save_scope(request: Request, user_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        actor = core.require_admin(request, db)
        target = db.get(User, user_id)
        if target is None:
            raise HTTPException(404)
        valid = {str(c.id) for c in db.scalars(select(Customer))}
        chosen = sorted({str(v) for v in form.getlist("customer_ids") if str(v) in valid})
        before = list(target.customer_scope or [])
        target.customer_scope = chosen or None
        core.add_event(db, "USER_CUSTOMER_SCOPE_CHANGED", actor=actor,
                       details={"target_user_id": str(target.id), "username": target.username, "before": before, "after": chosen,
                                "effective": target.role != "admin"}, source="portal")
        db.commit()
    return RedirectResponse("/admin/users?status=scope_saved#users", status_code=303)


def _all_customers():
    with SessionLocal() as db:
        return [(str(c.id), c.name) for c in db.scalars(select(Customer).order_by(Customer.name))]


def install_customer_scope(app=None) -> None:
    """Install after every other current_user wrapper (login security, 2FA)."""
    from app import ui_extension

    _discover_models()
    core.current_user = _wrap(core.current_user)
    ui_extension._current_user = _wrap(ui_extension._current_user)
    event.listen(Session, "do_orm_execute", _filter)
    event.listen(Session, "before_flush", _adopt_new_customers)
    core.templates.env.globals.update(customer_scope_of=user_scope, scope_customer_choices=_all_customers)
    if app is not None:
        app.include_router(router)
