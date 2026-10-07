"""Vendor lifecycle catalog and Device correlation (LIFE-01 / LIFE-02).

The catalog holds one record per vendor model with EOL/EOS dates, the source
they come from and when that source was checked.  Devices are correlated by
exact normalized model (or a declared alias) only: a partial match is reported
as ambiguous and never applied, so a Device never silently inherits the dates
of a different model.  Lifecycle values entered by hand stay manual until an
operator hands the Device back to the catalog.
"""
from __future__ import annotations

import csv
import io
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, or_, select

from app import main as core
from app.db import SessionLocal
from app.lifecycle_models import MATCH_LABELS, LifecycleRecord
from app.models import Device, utcnow
from app.security import validate_csrf
from app.ui_feedback import flash_redirect

router = APIRouter()
CATALOG_URL = "/security/lifecycle/catalog"
CSV_FIELDS = ["vendor", "model", "aliases", "eol_date", "eos_date", "source", "source_url", "evidence_date", "notes"]
LIFECYCLE_STATES = ("supported", "eol", "eos", "unknown")
UNMATCHED = ("ambiguous", "no_record", "no_model")
MIN_PREFIX = 4


# ------------------------------------------------------------ correlation --

def vendor_key(vendor) -> str:
    return str(vendor or "").strip().lower()


def model_key(vendor, model) -> str:
    key = re.sub(r"\s+", "", str(model or "")).upper()
    if vendor_key(vendor) == "mikrotik" and key.startswith("ROUTERBOARD"):
        # RouterOS reports older boards as "RouterBOARD 3011UiAS" = RB3011UiAS.
        key = "RB" + key[len("ROUTERBOARD"):]
    return key


def record_status(record: LifecycleRecord, today: date) -> str:
    if record.eos_date and record.eos_date <= today:
        return "eos"
    if record.eol_date and record.eol_date <= today:
        return "eol"
    return "supported"


@dataclass
class Correlation:
    match: str
    record: LifecycleRecord | None = None
    candidates: list = field(default_factory=list)


class CatalogIndex:
    def __init__(self, records):
        self.keys: dict = {}
        for record in records:
            vendor = vendor_key(record.vendor)
            for key in [record.model_key, *(model_key(vendor, a) for a in record.aliases or [])]:
                if key:
                    self.keys.setdefault(vendor, {})[key] = record

    def correlate(self, vendor, model) -> Correlation:
        vendor = vendor_key(vendor)
        key = model_key(vendor, model)
        if not key:
            return Correlation("no_model")
        keys = self.keys.get(vendor, {})
        if key in keys:
            return Correlation("catalog", keys[key])
        candidates = sorted(
            {r.model for k, r in keys.items() if min(len(k), len(key)) >= MIN_PREFIX and (k.startswith(key) or key.startswith(k))}
        )
        return Correlation("ambiguous", candidates=candidates) if candidates else Correlation("no_record")


def load_index(db) -> CatalogIndex:
    return CatalogIndex(db.scalars(select(LifecycleRecord)))


def _evidence_dt(value: date | None):
    return datetime.combine(value, time(), tzinfo=UTC) if value else None


def reconcile(db, now=None, device_ids=None) -> dict:
    """Apply the catalog to every non-manual Device; returns counters."""
    now = now or utcnow()
    today = now.date()
    index = load_index(db)
    query = select(Device).where(or_(Device.lifecycle_match.is_(None), Device.lifecycle_match != "manual"))
    if device_ids is not None:
        query = query.where(Device.id.in_(list(device_ids) or [None]))
    stats: Counter = Counter()
    for device in db.scalars(query):
        result = index.correlate(device.vendor, device.model)
        stats[result.match] += 1
        record = result.record
        values = {
            "lifecycle_match": result.match,
            "lifecycle_record_id": record.id if record else None,
            "lifecycle_status": record_status(record, today) if record else "unknown",
            "eol_date": record.eol_date if record else None,
            "eos_date": record.eos_date if record else None,
            "lifecycle_source": (record.source_url or record.source) if record else None,
            "lifecycle_verified_at": _evidence_dt(record.evidence_date) if record else None,
        }
        previous = device.lifecycle_status
        changed = False
        for name, value in values.items():
            if getattr(device, name) != value:
                setattr(device, name, value)
                changed = True
        if changed:
            stats["updated"] += 1
        if previous != device.lifecycle_status:
            stats["status_changed"] += 1
            core.add_event(
                db, "LIFECYCLE_STATUS_CHANGED", customer_id=device.customer_id, device_id=device.id,
                details={
                    "from": previous, "to": device.lifecycle_status, "match": result.match, "model": device.model,
                    "record": record.model if record else None, "source": device.lifecycle_source,
                    "evidence_date": record.evidence_date.isoformat() if record else None,
                },
            )
    from app.lifecycle_remediation import housekeeping as remediation_housekeeping  # LIFE-03

    db.flush()
    remediation = remediation_housekeeping(db, now)
    result = {k: stats.get(k, 0) for k in ("catalog", "ambiguous", "no_record", "no_model", "updated", "status_changed")}
    result["remediation_issues_opened"] = remediation["issues_opened"]
    return result


def run_scheduled_reconcile(now=None) -> dict:
    """Worker entry point: dates passing turn supported models into EOL/EOS."""
    with SessionLocal() as db:
        stats = reconcile(db, now)
        db.commit()
        return stats


# ------------------------------------------------------------- validation --

def _date(value, label):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{label}: data non valida «{text}» (formato AAAA-MM-GG).") from None


def parse_record(raw: dict, today: date) -> dict:
    vendor = vendor_key(raw.get("vendor"))
    model = re.sub(r"\s+", " ", str(raw.get("model") or "")).strip()
    source = str(raw.get("source") or "").strip()
    if not vendor or not model:
        raise ValueError("Vendor e modello sono obbligatori.")
    if not source:
        raise ValueError("La fonte è obbligatoria: indica da dove provengono le date.")
    evidence = _date(raw.get("evidence_date"), "Data verifica fonte")
    if not evidence:
        raise ValueError("Indica quando la fonte è stata verificata.")
    if evidence > today:
        raise ValueError("La data di verifica della fonte non può essere futura.")
    eol = _date(raw.get("eol_date"), "EOL")
    eos = _date(raw.get("eos_date"), "EOS")
    if eol and eos and eos < eol:
        raise ValueError("La data EOS non può precedere la data EOL.")
    url = str(raw.get("source_url") or "").strip() or None
    if url and not re.match(r"^https?://", url):
        raise ValueError("Il link della fonte deve iniziare con http:// o https://.")
    aliases = [a.strip() for a in re.split(r"[|\n,]", str(raw.get("aliases") or "")) if a.strip()]
    return {
        "vendor": vendor[:60], "model": model[:150], "model_key": model_key(vendor, model)[:150],
        "aliases": sorted(set(aliases), key=str.lower), "eol_date": eol, "eos_date": eos,
        "source": source[:160], "source_url": url[:500] if url else None, "evidence_date": evidence,
        "notes": str(raw.get("notes") or "").strip() or None,
    }


def _check_keys(db, data: dict, own_id=None) -> None:
    """A model or alias may identify one record only, or correlation would be ambiguous."""
    wanted = {data["model_key"], *(model_key(data["vendor"], a) for a in data["aliases"])}
    for other in db.scalars(select(LifecycleRecord).where(LifecycleRecord.vendor == data["vendor"])):
        if other.id == own_id:
            continue
        taken = {other.model_key, *(model_key(other.vendor, a) for a in other.aliases or [])}
        if wanted & taken:
            raise ValueError(f"Modello o alias già presente nel record «{other.model}».")


def _apply(record: LifecycleRecord, data: dict, user, now) -> None:
    for name, value in data.items():
        setattr(record, name, value)
    record.updated_at = now
    record.updated_by_user_id = user.id if user else None


# ------------------------------------------------------------------ pages --

def _reader(request, db):
    user = core.current_user(request, db)
    if user and not core.has_permission(user, "security.read"):
        raise HTTPException(403)
    return user


@router.get(CATALOG_URL, response_class=HTMLResponse, name="lifecycle_catalog")
def catalog(request: Request, q: str = "", vendor: str = ""):
    with SessionLocal() as db:
        user = _reader(request, db)
        if not user:
            return core.login_redirect()
        query = select(LifecycleRecord).order_by(LifecycleRecord.vendor, LifecycleRecord.model)
        if vendor:
            query = query.where(LifecycleRecord.vendor == vendor_key(vendor))
        if q.strip():
            query = query.where(LifecycleRecord.model.ilike(f"%{q.strip()}%") | LifecycleRecord.notes.ilike(f"%{q.strip()}%"))
        records = list(db.scalars(query))
        usage = dict(db.execute(select(Device.lifecycle_record_id, func.count(Device.id)).where(Device.lifecycle_record_id.is_not(None)).group_by(Device.lifecycle_record_id)).all())
        today = utcnow().date()
        return core.render(
            request, db, user, "lifecycle_catalog.html", title="Catalogo lifecycle",
            rows=[(r, record_status(r, today), usage.get(r.id, 0)) for r in records],
            vendors=sorted({v for v in db.scalars(select(LifecycleRecord.vendor).distinct()) if v}),
            q=q.strip(), vendor_filter=vendor, total=db.scalar(select(func.count(LifecycleRecord.id))) or 0,
            csv_fields=CSV_FIELDS,
        )


def _form(request, db, user, record=None, prefill=None):
    return core.render(
        request, db, user, "lifecycle_record_form.html", title="Record lifecycle",
        record=record, prefill=prefill or {},
        vendors=sorted({v for v in db.scalars(select(Device.vendor).distinct()) if v} | {"mikrotik", "ubiquiti"}),
        today=utcnow().date().isoformat(),
    )


@router.get(CATALOG_URL + "/new", response_class=HTMLResponse, name="lifecycle_record_new")
def record_new(request: Request, vendor: str = "", model: str = ""):
    with SessionLocal() as db:
        user = core.require_permission(request, db, "lifecycle.manage")
        return _form(request, db, user, prefill={"vendor": vendor_key(vendor), "model": model.strip()})


@router.get(CATALOG_URL + "/{record_id}/edit", response_class=HTMLResponse, name="lifecycle_record_edit")
def record_edit(request: Request, record_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.require_permission(request, db, "lifecycle.manage")
        record = db.get(LifecycleRecord, record_id)
        if not record:
            raise HTTPException(404)
        return _form(request, db, user, record=record)


@router.post(CATALOG_URL, name="lifecycle_record_save")
async def record_save(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "lifecycle.manage")
        record_id = form.get("record_id")
        record = db.get(LifecycleRecord, uuid.UUID(str(record_id))) if record_id else None
        back = f"{CATALOG_URL}/{record.id}/edit" if record else f"{CATALOG_URL}/new"
        now = utcnow()
        try:
            data = parse_record(dict(form), now.date())
            _check_keys(db, data, record.id if record else None)
        except ValueError as exc:
            return flash_redirect(request, back, "warning", str(exc), title="Record non salvato")
        created = record is None
        if created:
            record = LifecycleRecord(id=uuid.uuid4(), created_at=now)
            db.add(record)
        _apply(record, data, user, now)
        db.flush()
        core.add_event(
            db, "LIFECYCLE_RECORD_SAVED", actor=user,
            details={"record_id": str(record.id), "vendor": record.vendor, "model": record.model, "created": created,
                     "eol": data["eol_date"] and data["eol_date"].isoformat(), "eos": data["eos_date"] and data["eos_date"].isoformat(),
                     "source": record.source, "evidence_date": record.evidence_date.isoformat()},
        )
        stats = reconcile(db, now)
        db.commit()
        linked = db.scalar(select(func.count(Device.id)).where(Device.lifecycle_record_id == record.id)) or 0
        model = record.model
    return flash_redirect(
        request, CATALOG_URL, "success",
        f"{model}: {linked} apparati correlati; {stats['status_changed']} cambi di stato lifecycle.", title="Record salvato",
    )


@router.post(CATALOG_URL + "/{record_id}/delete", name="lifecycle_record_delete")
async def record_delete(request: Request, record_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "lifecycle.manage")
        record = db.get(LifecycleRecord, record_id)
        if not record:
            raise HTTPException(404)
        core.add_event(db, "LIFECYCLE_RECORD_DELETED", actor=user, details={"vendor": record.vendor, "model": record.model, "source": record.source})
        db.execute(Device.__table__.update().where(Device.lifecycle_record_id == record.id).values(lifecycle_record_id=None))
        db.delete(record)
        db.flush()
        stats = reconcile(db)
        db.commit()
    return flash_redirect(request, CATALOG_URL, "success", f"Record eliminato; {stats['status_changed']} apparati tornati senza dato lifecycle.", title="Record eliminato")


@router.get(CATALOG_URL + ".csv", name="lifecycle_catalog_csv")
def catalog_csv(request: Request):
    with SessionLocal() as db:
        user = _reader(request, db)
        if not user:
            return core.login_redirect()
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for r in db.scalars(select(LifecycleRecord).order_by(LifecycleRecord.vendor, LifecycleRecord.model)):
            writer.writerow({
                "vendor": r.vendor, "model": r.model, "aliases": "|".join(r.aliases or []),
                "eol_date": r.eol_date or "", "eos_date": r.eos_date or "", "source": r.source,
                "source_url": r.source_url or "", "evidence_date": r.evidence_date, "notes": r.notes or "",
            })
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="nsm-lifecycle-catalog.csv"'})


def import_rows(db, text: str, user, now) -> tuple[int, int]:
    """Validate the whole file first; nothing is written when any row is invalid."""
    reader = csv.DictReader(io.StringIO(text))
    missing = {"vendor", "model", "source", "evidence_date"} - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"Colonne mancanti: {', '.join(sorted(missing))}.")
    parsed, errors, seen = [], [], {}
    for line, raw in enumerate(reader, start=2):
        try:
            data = parse_record(raw, now.date())
            keys = {data["model_key"], *(model_key(data["vendor"], a) for a in data["aliases"])}
            for key in keys:
                if (data["vendor"], key) in seen and seen[(data["vendor"], key)] != data["model_key"]:
                    raise ValueError("modello o alias ripetuto in un'altra riga del file.")
            for key in keys:
                seen[(data["vendor"], key)] = data["model_key"]
            parsed.append((line, data))
        except ValueError as exc:
            errors.append(f"riga {line}: {exc}")
    if not parsed and not errors:
        raise ValueError("Il file non contiene righe.")
    existing = {(r.vendor, r.model_key): r for r in db.scalars(select(LifecycleRecord))}
    if not errors:
        for line, data in parsed:
            try:
                own = existing.get((data["vendor"], data["model_key"]))
                _check_keys(db, data, own.id if own else None)
            except ValueError as exc:
                errors.append(f"riga {line}: {exc}")
    if errors:
        raise ValueError("; ".join(errors[:5]) + (f" (e altri {len(errors) - 5})" if len(errors) > 5 else ""))
    created = updated = 0
    for _, data in parsed:
        record = existing.get((data["vendor"], data["model_key"]))
        if record is None:
            record = LifecycleRecord(id=uuid.uuid4(), created_at=now)
            db.add(record)
            existing[(data["vendor"], data["model_key"])] = record
            created += 1
        else:
            updated += 1
        _apply(record, data, user, now)
        db.flush()
    return created, updated


@router.post(CATALOG_URL + "/import", name="lifecycle_catalog_import")
async def catalog_import(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    upload = form.get("file")
    with SessionLocal() as db:
        user = core.require_permission(request, db, "lifecycle.manage")
        if upload is None or not hasattr(upload, "read"):
            return flash_redirect(request, CATALOG_URL, "warning", "Seleziona un file CSV.", title="Import non eseguito")
        content = await upload.read()
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            return flash_redirect(request, CATALOG_URL, "warning", "Il file deve essere CSV in UTF-8.", title="Import non eseguito")
        now = utcnow()
        try:
            created, updated = import_rows(db, text, user, now)
        except ValueError as exc:
            db.rollback()
            return flash_redirect(request, CATALOG_URL, "warning", f"Nessun record importato. {exc}", title="Import non eseguito")
        core.add_event(db, "LIFECYCLE_CATALOG_IMPORTED", actor=user, details={"created": created, "updated": updated, "filename": getattr(upload, "filename", None)})
        stats = reconcile(db, now)
        db.commit()
    return flash_redirect(
        request, CATALOG_URL, "success",
        f"{created} record creati, {updated} aggiornati; {stats['catalog']} apparati correlati, {stats['status_changed']} cambi di stato.",
        title="Catalogo importato",
    )


# ------------------------------------------------------ per-device values --

@router.get("/devices/{device_id}/lifecycle", response_class=HTMLResponse, name="device_lifecycle_form")
def device_lifecycle_form(request: Request, device_id: uuid.UUID):
    from app.lifecycle_remediation import page_context

    with SessionLocal() as db:
        user = core.require_permission(request, db, "security.read")
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        candidates = load_index(db).correlate(device.vendor, device.model)
        return core.render(
            request, db, user, "device_lifecycle_form.html", title="Lifecycle apparato",
            device=device, correlation=candidates, match_labels=MATCH_LABELS, states=LIFECYCLE_STATES,
            today=utcnow().date().isoformat(), can_manage=core.has_permission(user, "lifecycle.manage"),
            **page_context(db, device),
        )


@router.post("/devices/{device_id}/lifecycle", name="device_lifecycle_save")
async def device_lifecycle_save(request: Request, device_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "lifecycle.manage")
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        back = f"/devices/{device.id}/lifecycle"
        now = utcnow()
        if form.get("action") == "catalog":
            device.lifecycle_match = None
            db.flush()
            reconcile(db, now, [device.id])
            core.add_event(db, "LIFECYCLE_MANUAL_CLEARED", actor=user, customer_id=device.customer_id, device_id=device.id, details={"match": device.lifecycle_match})
            db.commit()
            label = MATCH_LABELS.get(device.lifecycle_match, device.lifecycle_match)
            return flash_redirect(request, f"/devices/{device.id}", "success", f"Lifecycle dal catalogo: {label}.", title="Lifecycle aggiornato")
        status = str(form.get("lifecycle_status") or "")
        source = str(form.get("source") or "").strip()
        try:
            if status not in LIFECYCLE_STATES:
                raise ValueError("Seleziona lo stato lifecycle.")
            if not source:
                raise ValueError("Indica la fonte del dato (documento, link, comunicazione del vendor).")
            evidence = _date(form.get("evidence_date"), "Data verifica")
            if not evidence or evidence > now.date():
                raise ValueError("Indica quando il dato è stato verificato (non nel futuro).")
            eol = _date(form.get("eol_date"), "EOL")
            eos = _date(form.get("eos_date"), "EOS")
            if eol and eos and eos < eol:
                raise ValueError("La data EOS non può precedere la data EOL.")
        except ValueError as exc:
            return flash_redirect(request, back, "warning", str(exc), title="Dato non salvato")
        previous = device.lifecycle_status
        device.lifecycle_match = "manual"
        device.lifecycle_record_id = None
        device.lifecycle_status = status
        device.eol_date, device.eos_date = eol, eos
        device.lifecycle_source = source[:255]
        device.lifecycle_verified_at = _evidence_dt(evidence)
        core.add_event(
            db, "LIFECYCLE_MANUAL_SET", actor=user, customer_id=device.customer_id, device_id=device.id,
            details={"from": previous, "to": status, "eol": eol and eol.isoformat(), "eos": eos and eos.isoformat(), "source": source[:255], "evidence_date": evidence.isoformat()},
        )
        from app.lifecycle_remediation import housekeeping as remediation_housekeeping

        db.flush()
        remediation_housekeeping(db, now)
        db.commit()
    return flash_redirect(request, f"/devices/{device_id}", "success", "Valore lifecycle manuale salvato con fonte e data di verifica.", title="Lifecycle aggiornato")


def install_lifecycle_catalog(app) -> None:
    app.include_router(router)
