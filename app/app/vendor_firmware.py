"""Firmware catalogs for manufacturers other than MikroTik (VEND-01).

One catalog per brand present in the inventory (a brand with no device has no
catalog).  Sources, all official:

- **uisp** — the latest version the UISP console reports for each Ubiquiti
  model (``inventory_data["uisp_firmware"]``), refreshed at every evaluation;
- **vendor_api** — Ubiquiti's public firmware update service
  (``fw-update.ubnt.com``) for the airOS platforms found in the inventory
  (``XC``, ``WA``, ``XW``…, read from the installed firmware string);
- **operator** — releases entered from the manufacturer's official download
  page (TP-Link, Tenda, Cambium, Huawei, …): version, models, date, notes link
  and whether it fixes security issues.  NSM never guesses a version.

Evaluation compares every device of the brand with the newest release that
matches its model (and platform) and sets the firmware state used by the
firmware worklist: *update_available*, *security_update* or *current*.  Devices
whose state comes from UISP (UBNT-06) keep the UISP view.
"""
from __future__ import annotations

import fnmatch
import re
import uuid
from datetime import date, datetime, timedelta

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app import main as core
from app import vendor_cpe
from app.db import SessionLocal
from app.models import Device, utcnow
from app.security import validate_csrf
from app.ui_feedback import flash_redirect
from app.vendor_firmware_models import VendorFirmwareRelease

router = APIRouter()
PAGE = "/operations/firmware/catalogs"
UBNT_API = "https://fw-update.ubnt.com/api/firmware-latest"
UBNT_REFRESH = timedelta(hours=24)
SOURCE_LABELS = {"uisp": "UISP", "vendor_api": "API ufficiale", "operator": "operatore"}
# Official download / support pages shown next to each catalog.
OFFICIAL_PAGES = {
    "ubiquiti": "https://www.ui.com/download",
    "tp-link": "https://www.tp-link.com/support/download/",
    "cambium": "https://support.cambiumnetworks.com/files",
    "tenda": "https://www.tendacn.com/download",
    "huawei": "https://support.huawei.com/enterprise/",
    "juniper": "https://support.juniper.net/support/downloads/",
    "cisco": "https://software.cisco.com/download/home",
    "fortinet": "https://support.fortinet.com/",
}
_AIROS = re.compile(r"^([A-Z0-9]{2,4})\.[A-Za-z0-9_-]+\.v(\d+\.\d+(?:\.\d+)?)")
_last_vendor_attempt: datetime | None = None


def platform_of(device) -> str:
    match = _AIROS.match(str(device.firmware_version or ""))
    return match.group(1) if match else ""


def version_key(brand: str, value) -> tuple | None:
    text = str(value or "")
    match = _AIROS.match(text)
    if match:
        text = match.group(2)
    parsed = vendor_cpe.version_tuple(text.lstrip("vV"))
    if parsed is None:
        return None
    # airOS strings carry build numbers after major.minor.patch: compare the release part only.
    return parsed[:3] if brand == "ubiquiti" else parsed


def is_newer(brand: str, candidate, installed) -> bool | None:
    a, b = version_key(brand, candidate), version_key(brand, installed)
    if a is None or b is None:
        return None
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def brands_in_inventory(db) -> dict[str, int]:
    counts: dict[str, int] = {}
    for device in db.scalars(select(Device)):
        brand = vendor_cpe.brand(device)
        if brand and brand != "mikrotik":
            counts[brand] = counts.get(brand, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def matches(release: VendorFirmwareRelease, device) -> bool:
    if release.brand != vendor_cpe.brand(device):
        return False
    if release.platform and release.platform != platform_of(device):
        return False
    pattern = (release.model_pattern or "").strip().lower()
    if not pattern:
        return True
    model = str(device.model or "").strip().lower()
    return bool(model) and (fnmatch.fnmatch(model, pattern) or (not any(c in pattern for c in "*?[") and pattern in model))


def best_release(releases, device):
    brand = vendor_cpe.brand(device)
    candidates = [r for r in releases if matches(r, device) and version_key(brand, r.version) is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda r: (version_key(brand, r.version) + (0,) * 6)[:6])


# --- Sources -----------------------------------------------------------------------------------

def _upsert(db, *, brand, version, source, model_pattern="", platform="", released_at=None, notes_url=None, notes=None, security=False, now=None):
    now = now or utcnow()
    row = db.scalar(select(VendorFirmwareRelease).where(
        VendorFirmwareRelease.brand == brand, VendorFirmwareRelease.model_pattern == model_pattern, VendorFirmwareRelease.platform == platform,
        VendorFirmwareRelease.version == version, VendorFirmwareRelease.source == source))
    if row is None:
        row = VendorFirmwareRelease(brand=brand, model_pattern=model_pattern, platform=platform, version=version, source=source,
                                    released_at=released_at, notes_url=notes_url, notes=notes, security=security, created_at=now)
        db.add(row)
        created = True
    else:
        created = False
    row.observed_at = now
    if released_at and not row.released_at:
        row.released_at = released_at
    if notes_url and not row.notes_url:
        row.notes_url = notes_url
    return created


def refresh_uisp(db, now=None) -> int:
    """Latest versions reported by UISP, per Ubiquiti model."""
    created = 0
    for device in db.scalars(select(Device).where(Device.vendor == "ubiquiti")):
        latest = ((device.inventory_data or {}).get("uisp_firmware") or {}).get("latest")
        if latest and device.model and version_key("ubiquiti", latest):
            created += _upsert(db, brand="ubiquiti", version=str(latest).lstrip("vV")[:80], source="uisp", model_pattern=str(device.model).strip().lower()[:120], now=now)
    db.flush()
    return created


def _ubnt_rows(payload) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    rows = (payload.get("_embedded") or {}).get("firmware") or payload.get("firmware") or []
    return [r for r in rows if isinstance(r, dict)]


def refresh_vendor_api(db, now=None, transport=None) -> dict:
    """Ubiquiti public firmware service for the airOS platforms found in the inventory."""
    now = now or utcnow()
    platforms = sorted({platform_of(d) for d in db.scalars(select(Device).where(Device.vendor == "ubiquiti")) if platform_of(d)})
    stats = {"platforms": len(platforms), "created": 0, "errors": []}
    if not platforms:
        return stats
    with httpx.Client(timeout=20.0, transport=transport, follow_redirects=False, headers={"Accept": "application/json"}) as client:
        for platform in platforms:
            params = [("filter", "eq~~product~~airos"), ("filter", f"eq~~platform~~{platform}"), ("filter", "eq~~channel~~release")]
            try:
                response = client.get(UBNT_API, params=params)
                response.raise_for_status()
                rows = _ubnt_rows(response.json())
            except (httpx.HTTPError, ValueError) as exc:
                stats["errors"].append(f"{platform}: {type(exc).__name__}")
                continue
            for row in rows:
                version = str(row.get("version") or "").lstrip("vV").split("+")[0]
                if not version_key("ubiquiti", version):
                    continue
                created = str(row.get("created") or "")[:10]
                try:
                    released = date.fromisoformat(created) if created else None
                except ValueError:
                    released = None
                link = ((row.get("_links") or {}).get("changelog") or {}).get("href")
                stats["created"] += _upsert(db, brand="ubiquiti", version=version[:80], source="vendor_api", platform=platform, released_at=released,
                                            notes_url=link if str(link or "").startswith("https://") else None, now=now)
    db.flush()
    return stats


# --- Evaluation --------------------------------------------------------------------------------

def evaluate(db, now=None) -> dict:
    now = now or utcnow()
    releases = list(db.scalars(select(VendorFirmwareRelease)))
    stats = {"evaluated": 0, "outdated": 0, "security": 0, "current": 0, "changed": 0}
    for device in db.scalars(select(Device)):
        brand = vendor_cpe.brand(device)
        if not brand or brand == "mikrotik" or not device.firmware_version:
            continue
        data = dict(device.inventory_data or {})
        if (data.get("uisp_firmware") or {}).get("latest"):
            continue  # UISP decides for the devices it manages (UBNT-06)
        release = best_release(releases, device)
        if release is None:
            if data.get("vendor_firmware"):  # the release that set the state was removed
                data.pop("vendor_firmware")
                device.inventory_data = data
                device.firmware_status, device.recommended_firmware_version = "unknown", None
                stats["changed"] += 1
            continue
        stats["evaluated"] += 1
        newer = is_newer(brand, release.version, device.firmware_version)
        if newer is None:
            continue
        if newer:
            status = "security_update" if release.security else "update_available"
            recommended = release.version
            stats["security" if release.security else "outdated"] += 1
        else:
            status, recommended = "current", None
            stats["current"] += 1
        info = {"latest": release.version, "source": release.source, "release_id": str(release.id), "security": bool(release.security), "evaluated_at": now.isoformat()}
        if device.firmware_status != status or device.recommended_firmware_version != recommended or (data.get("vendor_firmware") or {}).get("latest") != release.version:
            device.firmware_status, device.recommended_firmware_version = status, recommended
            stats["changed"] += 1
        data["vendor_firmware"] = info
        device.inventory_data = data
    return stats


def run_scheduled(now=None, transport=None) -> dict:
    global _last_vendor_attempt
    now = now or utcnow()
    with SessionLocal() as db:
        stats = {"uisp": refresh_uisp(db, now)}
        if _last_vendor_attempt is None or now - _last_vendor_attempt >= UBNT_REFRESH:
            _last_vendor_attempt = now
            stats["vendor_api"] = refresh_vendor_api(db, now, transport)
        stats["evaluation"] = evaluate(db, now)
        db.commit()
    return stats


# --- Pages -------------------------------------------------------------------------------------

def _context(db):
    brands = brands_in_inventory(db)
    releases = list(db.scalars(select(VendorFirmwareRelease).where(VendorFirmwareRelease.brand.in_(list(brands) or [""]))))
    devices = [d for d in db.scalars(select(Device)) if vendor_cpe.brand(d) in brands]
    catalogs = []
    for brand, count in brands.items():
        own = [d for d in devices if vendor_cpe.brand(d) == brand]
        rows = sorted([r for r in releases if r.brand == brand], key=lambda r: ((version_key(brand, r.version) or ()) + (0,) * 6)[:6], reverse=True)
        catalogs.append({
            "brand": brand, "label": vendor_cpe.label(brand), "devices": count, "page": OFFICIAL_PAGES.get(brand),
            "outdated": sum(1 for d in own if d.firmware_status in ("update_available", "security_update")),
            "security": sum(1 for d in own if d.firmware_status == "security_update"),
            "unknown_version": sum(1 for d in own if not d.firmware_version),
            "releases": rows[:200],
        })
    return {"catalogs": catalogs, "source_labels": SOURCE_LABELS}


@router.get(PAGE, response_class=HTMLResponse, name="vendor_firmware_catalogs")
def catalogs_page(request: Request):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "firmware.read"):
            raise HTTPException(403)
        return core.render(request, db, user, "vendor_firmware.html", title="Cataloghi firmware", **_context(db))


@router.post(f"{PAGE}/releases", name="vendor_firmware_add")
async def add_release(request: Request):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "firmware.execute")
        brand = str(form.get("brand") or "").strip().lower()
        version = str(form.get("version") or "").strip().lstrip("vV")[:80]
        pattern = str(form.get("model_pattern") or "").strip().lower()[:120]
        url = str(form.get("notes_url") or "").strip()[:500]
        released = str(form.get("released_at") or "").strip()
        if brand not in brands_in_inventory(db):
            return flash_redirect(request, PAGE, "danger", "Il catalogo esiste solo per i produttori presenti nell'inventario.", title="Release non aggiunta")
        if not version_key(brand, version):
            return flash_redirect(request, PAGE, "danger", "Versione non riconosciuta: usa il formato del produttore (es. 1.2.3 o V1.0.0 Build 20240101).", title="Release non aggiunta")
        if url and not url.startswith(("https://", "http://")):
            return flash_redirect(request, PAGE, "danger", "Il link alle note deve essere un indirizzo http(s) della pagina ufficiale.", title="Release non aggiunta")
        try:
            released_at = date.fromisoformat(released) if released else None
        except ValueError:
            return flash_redirect(request, PAGE, "danger", "Data di rilascio non valida.", title="Release non aggiunta")
        if not _upsert(db, brand=brand, version=version, source="operator", model_pattern=pattern, released_at=released_at, notes_url=url or None,
                       notes=str(form.get("notes") or "").strip()[:2000] or None, security=str(form.get("security") or "") == "1"):
            return flash_redirect(request, PAGE, "warning", "Questa release è già nel catalogo.", title="Release non aggiunta")
        db.flush()
        db.scalar(select(VendorFirmwareRelease).where(VendorFirmwareRelease.brand == brand, VendorFirmwareRelease.version == version,
                                                      VendorFirmwareRelease.model_pattern == pattern, VendorFirmwareRelease.source == "operator")).created_by_user_id = user.id
        core.add_event(db, "VENDOR_FIRMWARE_RELEASE_ADDED", actor=user, details={"brand": brand, "version": version, "models": pattern or "tutti",
                                                                               "security": str(form.get("security") or "") == "1"}, source="portal")
        stats = evaluate(db)
        db.commit()
    return flash_redirect(request, PAGE, "success", f"Release {version} aggiunta al catalogo {vendor_cpe.label(brand)}: {stats['outdated'] + stats['security']} apparati da aggiornare.",
                          title="Catalogo aggiornato")


@router.post(f"{PAGE}/releases/{{release_id}}/delete", name="vendor_firmware_delete")
async def delete_release(request: Request, release_id: uuid.UUID):
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        user = core.require_permission(request, db, "firmware.execute")
        row = db.get(VendorFirmwareRelease, release_id)
        if row is None or row.source != "operator":
            return flash_redirect(request, PAGE, "warning", "Si possono eliminare solo le release inserite dagli operatori.", title="Release non eliminata")
        core.add_event(db, "VENDOR_FIRMWARE_RELEASE_DELETED", actor=user, details={"brand": row.brand, "version": row.version}, source="portal")
        db.delete(row)
        db.flush()
        evaluate(db)
        db.commit()
    return flash_redirect(request, PAGE, "success", "Release eliminata dal catalogo.", title="Catalogo aggiornato")


@router.post(f"{PAGE}/refresh", name="vendor_firmware_refresh")
async def refresh(request: Request):
    global _last_vendor_attempt
    form = await request.form()
    validate_csrf(request, str(form.get("csrf") or ""))
    with SessionLocal() as db:
        core.require_permission(request, db, "firmware.execute")
    _last_vendor_attempt = None
    stats = run_scheduled()
    api = stats.get("vendor_api") or {}
    text = f"UISP: {stats['uisp']} nuove versioni; API Ubiquiti: {api.get('created', 0)} nuove su {api.get('platforms', 0)} piattaforme"
    if api.get("errors"):
        text += f" (non raggiungibile: {', '.join(api['errors'][:3])})"
    text += f"; {stats['evaluation']['evaluated']} apparati valutati."
    return flash_redirect(request, PAGE, "warning" if api.get("errors") else "success", text, title="Cataloghi aggiornati")


def install_vendor_firmware(app) -> None:
    app.include_router(router)
