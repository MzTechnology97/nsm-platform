"""Administrative CSV inventory import for Core 0.22.

The importer intentionally accepts inventory metadata only. It never accepts or
stores SSH/API passwords. MikroTik rows are created in ``pending_enrollment``
and must still pair through the outbound NSM agent.
"""

import csv
import io
import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import main as core
from app.db import SessionLocal
from app.models import Customer, Device, Site
from app.security import validate_csrf

router = APIRouter()
MAX_CSV_BYTES = 1024 * 1024
MAX_CSV_ROWS = 1000
CSV_COLUMNS = (
    "customer_code",
    "site",
    "vendor",
    "device_type",
    "name",
    "display_name",
    "device_identity",
    "management_ip",
    "primary_mac",
    "serial_number",
    "model",
    "firmware_version",
)
REQUIRED_COLUMNS = {"customer_code", "vendor", "device_type", "name"}
FORBIDDEN_CREDENTIAL_COLUMNS = {
    "password",
    "ssh_password",
    "ssh_user",
    "ssh_username",
    "api_key",
    "token",
    "secret",
    "snmp_community",
    "community",
}
_VENDOR_ALIASES = {
    "mikrotik": "mikrotik",
    "routeros": "mikrotik",
    "ubiquiti": "ubiquiti",
    "ubnt": "ubiquiti",
    "ui": "ubiquiti",
    "tplink": "tp-link",
    "tp-link": "tp-link",
    "tp_link": "tp-link",
}


@dataclass
class ImportRow:
    row_number: int
    status: str
    message: str
    data: dict
    device_id: str | None = None


def _clean(value, limit=255):
    value = str(value or "").strip()
    return value[:limit] if value else ""


def _normalize_vendor(value: str) -> str:
    raw = _clean(value, 60).lower()
    raw = _VENDOR_ALIASES.get(raw, raw)
    if not raw or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,59}", raw):
        raise ValueError("Vendor non valido.")
    return raw


def _normalize_ip(value: str):
    value = _clean(value, 255)
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise ValueError("IP di management non valido.") from exc


def _parse_csv(raw: bytes):
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(413, "CSV troppo grande. Limite: 1 MiB.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "Il CSV deve essere codificato UTF-8.") from exc
    if not text.strip():
        raise HTTPException(400, "CSV vuoto.")
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise HTTPException(400, "Intestazione CSV mancante.")
    normalized_fields = [str(name or "").strip().lower() for name in reader.fieldnames]
    if len(set(normalized_fields)) != len(normalized_fields):
        raise HTTPException(400, "Il CSV contiene colonne duplicate.")
    field_set = set(normalized_fields)
    credential_columns = sorted(field_set & FORBIDDEN_CREDENTIAL_COLUMNS)
    if credential_columns:
        raise HTTPException(
            400,
            "Colonne credenziali non consentite: " + ", ".join(credential_columns) + ". Usa l'enrollment NSM per-device.",
        )
    unknown = sorted(field_set - set(CSV_COLUMNS))
    if unknown:
        raise HTTPException(400, "Colonne non supportate: " + ", ".join(unknown))
    missing = sorted(REQUIRED_COLUMNS - field_set)
    if missing:
        raise HTTPException(400, "Colonne obbligatorie mancanti: " + ", ".join(missing))
    rows = []
    for index, raw_row in enumerate(reader, start=2):
        if index - 1 > MAX_CSV_ROWS:
            raise HTTPException(413, f"Troppe righe. Limite: {MAX_CSV_ROWS} apparati.")
        row = {}
        for original, normalized in zip(reader.fieldnames, normalized_fields):
            row[normalized] = raw_row.get(original, "")
        if not any(str(value or "").strip() for value in row.values()):
            continue
        rows.append((index, row))
    if not rows:
        raise HTTPException(400, "Il CSV non contiene apparati.")
    return rows


def _inventory_maps(db):
    customers = list(db.scalars(select(Customer).where(Customer.is_active.is_(True)).order_by(Customer.name)))
    customer_by_code = {str(c.code or "").strip().casefold(): c for c in customers if c.code}
    sites = list(db.scalars(select(Site).order_by(Site.name)))
    site_map = {}
    ambiguous_sites = set()
    for site in sites:
        key = (site.customer_id, site.name.strip().casefold())
        if key in site_map:
            ambiguous_sites.add(key)
        else:
            site_map[key] = site

    existing_mac = set()
    existing_serial = set()
    for vendor, mac, serial in db.execute(select(Device.vendor, Device.primary_mac, Device.serial_number)):
        vendor_key = str(vendor or "").casefold()
        if mac:
            existing_mac.add((vendor_key, str(mac).upper()))
        if serial:
            existing_serial.add((vendor_key, str(serial).strip().casefold()))
    return customer_by_code, site_map, ambiguous_sites, existing_mac, existing_serial


def _validate_rows(db, parsed_rows):
    customer_by_code, site_map, ambiguous_sites, seen_mac, seen_serial = _inventory_maps(db)
    results = []
    for row_number, raw in parsed_rows:
        data = {}
        try:
            customer_code = _clean(raw.get("customer_code"), 80)
            customer = customer_by_code.get(customer_code.casefold())
            if not customer:
                raise ValueError(f"Cliente con codice '{customer_code}' non trovato o disattivato.")

            site_name = _clean(raw.get("site"), 200)
            site = None
            if site_name:
                key = (customer.id, site_name.casefold())
                if key in ambiguous_sites:
                    raise ValueError(f"Più sedi del cliente hanno nome '{site_name}': usare un nome univoco.")
                site = site_map.get(key)
                if not site:
                    raise ValueError(f"Sede '{site_name}' non trovata per il cliente {customer_code}.")

            vendor = _normalize_vendor(raw.get("vendor"))
            device_type = _clean(raw.get("device_type"), 60).lower()
            name = _clean(raw.get("name"), 200)
            if not device_type:
                raise ValueError("device_type obbligatorio.")
            if not name:
                raise ValueError("name obbligatorio.")

            try:
                primary_mac = core.norm_mac(_clean(raw.get("primary_mac"), 64))
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
            serial = _clean(raw.get("serial_number"), 150) or None
            management_ip = _normalize_ip(raw.get("management_ip"))

            data = {
                "customer_id": customer.id,
                "customer_code": customer.code,
                "customer_name": customer.name,
                "site_id": site.id if site else None,
                "site_name": site.name if site else "",
                "vendor": vendor,
                "device_type": device_type,
                "name": name,
                "display_name": _clean(raw.get("display_name"), 200) or None,
                "device_identity": _clean(raw.get("device_identity"), 200) or None,
                "management_ip": management_ip,
                "primary_mac": primary_mac,
                "serial_number": serial,
                "model": _clean(raw.get("model"), 150) or None,
                "firmware_version": _clean(raw.get("firmware_version"), 150) or None,
            }

            duplicate_reasons = []
            mac_key = (vendor.casefold(), primary_mac) if primary_mac else None
            serial_key = (vendor.casefold(), serial.casefold()) if serial else None
            if mac_key and mac_key in seen_mac:
                duplicate_reasons.append(f"MAC {primary_mac}")
            if serial_key and serial_key in seen_serial:
                duplicate_reasons.append(f"seriale {serial}")
            if duplicate_reasons:
                results.append(ImportRow(row_number, "already_exists", "Già presente: " + ", ".join(duplicate_reasons), data))
                continue

            if mac_key:
                seen_mac.add(mac_key)
            if serial_key:
                seen_serial.add(serial_key)
            results.append(ImportRow(row_number, "ready", "Pronto per l'importazione.", data))
        except ValueError as exc:
            safe_data = {
                "customer_code": _clean(raw.get("customer_code"), 80),
                "site_name": _clean(raw.get("site"), 200),
                "vendor": _clean(raw.get("vendor"), 60),
                "device_type": _clean(raw.get("device_type"), 60),
                "name": _clean(raw.get("name"), 200),
                "management_ip": _clean(raw.get("management_ip"), 255),
                "primary_mac": _clean(raw.get("primary_mac"), 64),
                "serial_number": _clean(raw.get("serial_number"), 150),
            }
            results.append(ImportRow(row_number, "error", str(exc), safe_data))
    return results


def _import_ready_rows(db, user, rows):
    now = datetime.now(timezone.utc).isoformat()
    for item in rows:
        if item.status != "ready":
            continue
        d = item.data
        status = "pending_enrollment" if d["vendor"] == "mikrotik" else "pending_link"
        management_source = "mikrotik_agent" if d["vendor"] == "mikrotik" else "manual"
        try:
            with db.begin_nested():
                device = Device(
                    customer_id=d["customer_id"],
                    site_id=d["site_id"],
                    vendor=d["vendor"],
                    device_type=d["device_type"],
                    name=d["name"],
                    display_name=d["display_name"],
                    device_identity=d["device_identity"],
                    management_ip=d["management_ip"],
                    primary_mac=d["primary_mac"],
                    serial_number=d["serial_number"],
                    model=d["model"],
                    firmware_version=d["firmware_version"],
                    management_source=management_source,
                    inventory_source="csv_import",
                    inventory_data={"import_source": "csv", "imported_at": now},
                    status=status,
                )
                db.add(device)
                db.flush()
                core.add_event(
                    db,
                    "DEVICE_IMPORTED_FROM_CSV",
                    actor=user,
                    customer_id=device.customer_id,
                    device_id=device.id,
                    details={
                        "row": item.row_number,
                        "vendor": device.vendor,
                        "name": device.name,
                        "site_id": str(device.site_id) if device.site_id else None,
                        "status": status,
                        "credentials_imported": False,
                    },
                    source="csv_import",
                )
                item.device_id = str(device.id)
                item.status = "imported"
                item.message = "Importato."
        except IntegrityError:
            item.status = "error"
            item.message = "Conflitto con un apparato esistente durante il salvataggio."
    return rows


def _summary(rows):
    counts = {key: 0 for key in ("ready", "imported", "already_exists", "error")}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    counts["total"] = len(rows)
    return counts


def _render(request, db, user, rows=None, summary=None, filename=None, dry_run=True):
    return core.render(
        request,
        db,
        user,
        "device_csv_import.html",
        rows=rows or [],
        summary=summary,
        filename=filename,
        dry_run=dry_run,
        max_rows=MAX_CSV_ROWS,
        max_bytes=MAX_CSV_BYTES,
        csv_columns=CSV_COLUMNS,
    )


@router.get("/devices/import", response_class=HTMLResponse, name="device_csv_import_page")
def device_csv_import_page(request: Request):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        core.require_admin(user)
        return _render(request, db, user)


@router.get("/devices/import/template.csv", response_class=PlainTextResponse, name="device_csv_import_template")
def device_csv_import_template(request: Request):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        core.require_admin(user)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_COLUMNS)
    writer.writerow(["CLIENTE01", "POP Centro", "mikrotik", "router", "CCR Centro", "Core Centro", "", "192.0.2.10", "02:00:00:00:00:10", "", "CCR2004-1G-12S+2XS", ""])
    return PlainTextResponse(
        output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="nsm-device-import-template.csv"', "Cache-Control": "no-store"},
    )


@router.post("/devices/import", response_class=HTMLResponse, name="device_csv_import_submit")
async def device_csv_import_submit(
    request: Request,
    csv_file: UploadFile = File(...),
    mode: str = Form("validate"),
    csrf: str = Form(...),
):
    validate_csrf(request, csrf)
    mode = str(mode or "validate").strip().lower()
    if mode not in {"validate", "import"}:
        raise HTTPException(400, "Modalità import non valida.")

    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        core.require_admin(user)
        raw = await csv_file.read(MAX_CSV_BYTES + 1)
        parsed_rows = _parse_csv(raw)
        filename = _clean(csv_file.filename, 255) or "devices.csv"
        rows = _validate_rows(db, parsed_rows)
        if mode == "import":
            rows = _import_ready_rows(db, user, rows)
            summary = _summary(rows)
            core.add_event(
                db,
                "DEVICE_CSV_IMPORT_COMPLETED",
                actor=user,
                details={
                    "filename": filename,
                    "total": summary["total"],
                    "imported": summary.get("imported", 0),
                    "already_exists": summary.get("already_exists", 0),
                    "errors": summary.get("error", 0),
                    "credentials_imported": False,
                },
                source="csv_import",
            )
            db.commit()
            return _render(request, db, user, rows, summary, filename, dry_run=False)

        summary = _summary(rows)
        core.add_event(
            db,
            "DEVICE_CSV_IMPORT_VALIDATED",
            actor=user,
            details={
                "filename": filename,
                "total": summary["total"],
                "ready": summary.get("ready", 0),
                "already_exists": summary.get("already_exists", 0),
                "errors": summary.get("error", 0),
            },
            source="csv_import",
        )
        db.commit()
        return _render(request, db, user, rows, summary, filename, dry_run=True)


def install_device_csv_import(app):
    app.include_router(router)
