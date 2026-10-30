"""Firmware images uploaded into the vendor catalogs (VEND-01, operator request 2026-10-23).

The operator no longer types version, models and date: *Carica firmware* on a catalog takes the image itself
(``.bin``, ``.img``, ``.zip``, at most ``acs_firmware.MAX_IMAGE``) and NSM reads what it can:

- **TP-Link** — official name ``VX230v(EU1)_V1_0.19.0 Build 260323`` (the ``.zip`` of the site or its ``.bin``):
  model, hardware version (``V1``, the catalog platform) and version; the build is the release date (``YYMMDD``).
  A ``.zip`` must hold exactly one ``.bin``, which is the image kept;
- **Ubiquiti** — airOS ``XC.qca956x.v8.7.25.46619.240712.1445.bin``: platform (``XC``, ``WA``, ``XW``, ``XM``…) and
  version, from the name or else from the image header (which carries the same string); the date is ``YYMMDD``;
- **Cambium** — ePMP ``ePMP-AC-v4.8.1.img``: version, and the models of the inventory that belong to the family
  (``AC`` → ePMP 3000 / Force 300, ``AX`` → ePMP 4000 / Force 400, ``GPS_Synced`` → ePMP 1000-2000 / Force 1xx-2xx);
- any other brand — the version only.

Whatever cannot be read (version, models) is asked; the release date falls back to the file date sent by the
browser, then to today. The upload is staged (``storage_root()/firmware/.incoming/<token>``, 24 h) until the operator
confirms; the release is then recorded (source ``operator``) and the image kept under
``storage_root()/firmware/<release id>/`` (downloadable from the catalog). For TR-069 CPEs of matching model and
hardware the image is also put in the GenieACS file server, once per product class, so it is ready on the device
*Firmware* tab; nothing is ever installed from here. Audited ``VENDOR_FIRMWARE_RELEASE_ADDED`` with the file digest.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import time
import zipfile
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app import main as core
from app import vendor_cpe
from app import vendor_firmware as vf
from app.backup_storage import storage_root
from app.input_errors import InputError
from app.models import Device
from app.vendor_firmware_models import VendorFirmwareRelease

EXTENSIONS = (".bin", ".img", ".zip", ".tar", ".tar.gz", ".npk", ".pkg")
STAGING_TTL_SECONDS = 24 * 3600
HEADER_BYTES = 4096
_AIROS = re.compile(rb"([A-Z0-9]{2,4})\.(?:[A-Za-z0-9_-]+\.)?v(\d+\.\d+(?:\.\d+)?)(?:\.\d+)?(?:\.(\d{6}))?")
_YYMMDD = re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)")
_VERSION = re.compile(r"(?<![\d.])v?(\d+\.\d+(?:\.\d+){0,2})(?![\d])", re.I)
CAMBIUM_FAMILIES = {
    "ac": ("epmp 3000", "force 300", "force 3"),
    "ax": ("epmp 4", "force 4"),
    "gps_synced": ("epmp 1000", "epmp 2000", "force 1", "force 2"),
    "nongps_synced": ("epmp 1000", "force 1"),
}


def _root() -> Path:
    return storage_root() / "firmware"


def _staging() -> Path:
    return _root() / ".incoming"


def _date_from(text: str) -> date | None:
    for match in _YYMMDD.finditer(text or ""):
        try:
            found = date(2000 + int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
        if found <= date.today():
            return found
    return None


def inventory_models(db, brand: str) -> list[str]:
    return sorted({str(d.model).strip() for d in db.scalars(select(Device).where(Device.model.is_not(None)))
                   if vendor_cpe.brand(d) == brand and str(d.model).strip()}, key=str.lower)


def detect(brand: str, name: str, head: bytes, models: list[str]) -> dict:
    """What the name (and header) of an image say: version, model_pattern, platform, released_at; ``missing`` lists the rest."""
    stem = re.sub(r"\.(zip|bin|img|tar\.gz|tar|npk|pkg)$", "", os.path.basename(name or ""), flags=re.I)
    found: dict = {"version": None, "model_pattern": "", "platform": "", "released_at": None, "family": None}
    missing = []
    if brand == "tp-link":
        from app import tplink_firmware as tp

        match = tp._NAME.match(stem)
        if match and tp.version_key(match.group("version")):
            hardware = tp.hardware_key(match.group("hw"))
            found["version"] = re.sub(r"\s+", " ", match.group("version")).strip()
            found["model_pattern"] = match.group("model").strip().lower()[:120]
            found["platform"] = f"V{hardware[0]}" + (f".{hardware[1]}" if hardware[1] else "")
            build = tp._BUILD.search(match.group("version"))
            found["released_at"] = _date_from(build.group(1)) if build else None
        else:
            missing += ["version", "models"]
    elif brand == "ubiquiti":
        match = _AIROS.search(stem.encode()) or _AIROS.search(head or b"")
        if match:
            found["platform"] = match.group(1).decode()
            found["version"] = match.group(2).decode()
            found["released_at"] = _date_from(match.group(3).decode()) if match.group(3) else None
        else:
            missing.append("version")
    else:
        match = _VERSION.search(stem)
        if match:
            found["version"] = match.group(1)
        else:
            missing.append("version")
        if brand == "cambium":
            family = re.match(r"epmp[-_](ac|ax|gps_synced|nongps_synced)", stem, re.I)
            prefixes = CAMBIUM_FAMILIES.get(family.group(1).lower()) if family else None
            chosen = [m for m in models if prefixes and any(p in m.lower() for p in prefixes)]  # cnMaestro may say "ePMP Force 300"
            if chosen:
                found["family"] = family.group(1)
                found["model_pattern"] = "|".join(m.lower() for m in chosen)[:120]
            else:
                missing.append("models")
        else:
            missing.append("models")
        found["released_at"] = _date_from(stem) if found["version"] else None
    return {**found, "missing": missing}


def _clean_staging() -> None:
    folder = _staging()
    if not folder.exists():
        return
    for entry in folder.iterdir():
        try:
            if time.time() - entry.stat().st_mtime > STAGING_TTL_SECONDS:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


def _extract_single_bin(archive_path: Path, target_dir: Path, max_bytes: int) -> Path:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            bins = [i for i in archive.infolist() if not i.is_dir() and i.filename.lower().endswith((".bin", ".img"))]
            if len(bins) != 1:
                raise InputError("archive", f"L'archivio contiene {len(bins)} immagini (.bin/.img) invece di una: carica l'immagine direttamente.")
            entry = bins[0]
            out = target_dir / re.sub(r"[^A-Za-z0-9._()\- ]+", "_", os.path.basename(entry.filename))[:150]
            written = 0
            with archive.open(entry) as source, open(out, "wb") as sink:
                while chunk := source.read(1024 * 1024):
                    written += len(chunk)
                    if written > max_bytes:
                        raise InputError("too_large", "L'immagine nell'archivio supera la dimensione massima accettata.", 413)
                    sink.write(chunk)
            return out
    except zipfile.BadZipFile as exc:
        raise InputError("archive", "Il file .zip non è un archivio valido.") from exc


def stage(db, user, brand: str, name: str, source_path: str, file_date: str | None) -> dict:
    """Keep an uploaded image until confirmation and say what NSM read from it. ``source_path`` is moved."""
    from app import acs_firmware as fw

    brand = str(brand or "").strip().lower()
    if brand not in vf.brands_in_inventory(db):
        raise InputError("brand", "Il catalogo esiste solo per i produttori presenti nell'inventario.")
    original = os.path.basename(str(name or "").replace("\\", "/"))[:150] or "firmware.bin"
    if not original.lower().endswith(EXTENSIONS):
        raise InputError("extension", "Carica l'immagine del produttore (.bin, .img o .zip).")
    _clean_staging()
    token = secrets.token_urlsafe(18)
    folder = _staging() / token
    folder.mkdir(parents=True)
    staged = folder / re.sub(r"[^A-Za-z0-9._()\- ]+", "_", original)
    shutil.move(source_path, staged)
    image = _extract_single_bin(staged, folder, fw.MAX_IMAGE) if original.lower().endswith(".zip") else staged
    if image != staged:
        staged.unlink()
    digest = hashlib.sha256()
    with open(image, "rb") as handle:
        head = handle.read(HEADER_BYTES)
        digest.update(head)
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    models = inventory_models(db, brand)
    found = detect(brand, original, head, models)
    if found["version"] is None and image != staged:
        inner = detect(brand, image.name, head, models)
        if inner["version"]:
            found = inner
    try:
        uploaded_on = date.fromisoformat(str(file_date or "")[:10]) if file_date else None
    except ValueError:
        uploaded_on = None
    meta = {"brand": brand, "original": original, "image": image.name, "size": image.stat().st_size, "sha256": digest.hexdigest(),
            "user_id": str(user.id), "detected": {k: (v.isoformat() if isinstance(v, date) else v) for k, v in found.items()},
            "file_date": uploaded_on.isoformat() if uploaded_on else None}
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return {"token": token, **{k: meta[k] for k in ("brand", "original", "image", "size", "sha256")}, "detected": meta["detected"],
            "missing": found["missing"], "models": models, "released_default": (found["released_at"] or uploaded_on or date.today()).isoformat()}


def _acs_targets(db, release) -> list[Device]:
    """One TR-069 CPE per product class among the devices this release applies to."""
    seen, out = set(), []
    for device in db.scalars(select(Device).where(Device.management_source == "tr069", Device.external_device_id.is_not(None))):
        if not vf.matches(release, device):
            continue
        product = ((device.inventory_data or {}).get("tr069") or {}).get("product_class") or ""
        if product not in seen:
            seen.add(product)
            out.append(device)
    return out


def _push_to_acs(db, release, path: Path, original: str, digest: str) -> tuple[list[str], list[str]]:
    from app import acs_diagnostics as nbi
    from app import acs_firmware as fw
    from app import genieacs_connector as acs

    devices = _acs_targets(db, release)
    if not devices:
        return [], []
    connection = acs._connection(db)
    if connection is None or not connection.is_enabled:
        return [], ["GenieACS non è configurato: l'immagine resta solo nel catalogo."]
    stored, warnings = [], []
    for device in devices:
        try:
            identity = fw._identity(nbi._read(connection, device.external_device_id, fw.IDENTITY))
            name = fw.file_name_for(identity, original, digest)
            if name not in {f["name"] for f in fw.list_files(connection, identity.get("product_class"))}:
                fw.store_file(connection, identity, name, str(path), release.version)
            stored.append(name)
        except (nbi.AcsDiagnosticError, fw.FirmwareError) as exc:
            warnings.append(f"GenieACS ({device.model or 'CPE'}): {exc}")
    return stored, warnings


def confirm(db, user, token: str, form: dict) -> dict:
    """Record the staged image as an operator release (values read from it, completed by ``form``); commits."""
    folder = _staging() / re.sub(r"[^A-Za-z0-9_-]", "", str(token or ""))[:40]
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise InputError("expired", "Caricamento scaduto o non trovato: carica di nuovo il file.", 404) from None
    if meta["user_id"] != str(user.id):
        raise InputError("expired", "Caricamento scaduto o non trovato: carica di nuovo il file.", 404)
    brand, found = meta["brand"], meta["detected"]
    version = str(form.get("version") or found.get("version") or "").strip().lstrip("vV")[:80]
    if not vf.version_key(brand, version):
        raise InputError("version", "Versione non riconosciuta: indicala nel formato del produttore (es. 1.2.3 o 0.19.0 Build 260323).")
    if "model_pattern" in form or "models" in form:
        chosen = form.get("models")
        pattern = "|".join(str(m).strip().lower() for m in chosen if str(m).strip()) if isinstance(chosen, list) else str(form.get("model_pattern") or "")
    else:
        pattern = found.get("model_pattern") or ""
    pattern = pattern.strip().lower()[:120]
    platform = str(found.get("platform") or "")[:40]
    released = str(form.get("released_at") or found.get("released_at") or meta.get("file_date") or date.today().isoformat())
    try:
        released_at = date.fromisoformat(released[:10])
    except ValueError:
        raise InputError("date", "Data di rilascio non valida.") from None
    security = str(form.get("security") or "") in ("1", "true", "True", "on")
    created = vf._upsert(db, brand=brand, version=version, source="operator", model_pattern=pattern, platform=platform, released_at=released_at,
                         notes=str(form.get("notes") or "").strip()[:2000] or None, security=security)
    db.flush()
    release = db.scalar(select(VendorFirmwareRelease).where(
        VendorFirmwareRelease.brand == brand, VendorFirmwareRelease.version == version, VendorFirmwareRelease.model_pattern == pattern,
        VendorFirmwareRelease.platform == platform, VendorFirmwareRelease.source == "operator"))
    target = _root() / str(release.id)
    if not created and stored_file(release):
        raise InputError("exists", "Questa release è già nel catalogo con la sua immagine.", 409)
    release.created_by_user_id = release.created_by_user_id or user.id
    if security:
        release.security = True
    target.mkdir(parents=True, exist_ok=True)
    image = target / meta["image"]
    shutil.move(str(folder / meta["image"]), image)
    shutil.rmtree(folder, ignore_errors=True)
    acs_files, warnings = _push_to_acs(db, release, image, meta["image"], meta["sha256"])
    info = {"name": meta["image"], "original": meta["original"], "size": meta["size"], "sha256": meta["sha256"], "acs_files": acs_files,
            "by": user.username}
    (target / "meta.json").write_text(json.dumps(info), encoding="utf-8")
    core.add_event(db, "VENDOR_FIRMWARE_RELEASE_ADDED", actor=user, source="portal",
                   details={"brand": brand, "version": version, "models": pattern or "tutti", "platform": platform or None, "security": security,
                            "file": meta["image"], "size": meta["size"], "sha256": meta["sha256"], "acs_files": acs_files})
    stats = vf.evaluate(db)
    db.commit()
    return {"brand": brand, "version": version, "model_pattern": pattern or None, "platform": platform or None, "released_at": released_at.isoformat(),
            "to_update": stats["outdated"] + stats["security"], "acs_files": acs_files, "warnings": warnings}


def stored_file(release) -> dict | None:
    """The image kept for a release (name, size, sha256, GenieACS files), if any."""
    try:
        info = json.loads((_root() / str(release.id) / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if (_root() / str(release.id) / info.get("name", "")).is_file() else None


def file_path(release) -> Path | None:
    info = stored_file(release)
    return _root() / str(release.id) / info["name"] if info else None


def remove_files(release_id) -> None:
    shutil.rmtree(_root() / str(release_id), ignore_errors=True)
