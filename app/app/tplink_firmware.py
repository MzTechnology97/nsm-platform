"""TP-Link firmware from the official Italian support site (VEND-01, operator decision 2026-10-23).

TP-Link publishes no firmware API: each model has a download page
(``https://www.tp-link.com/it/support/download/<model>/[v<hardware>/]``) whose *Firmware* tab lists the images
(name such as ``VX230v(EU1)_V1_0.19.0 Build 260323``, publication date, ``.zip`` on ``static.tp-link.com``).

- Once a day (``vendor_firmware.run_scheduled``) NSM reads the page of every TP-Link model in the inventory, for
  the hardware version its devices report (``inventory_data["tr069"]["hardware_version"]``, e.g. ``VX230v v1.0``
  → ``V1``), and records each image as a catalog release (source ``vendor_site``, platform = hardware version), so
  the device page, the firmware list and the catalogs show the newest version like for the other brands. Images of
  another hardware version are ignored: they can damage the device.
- *Scarica dal sito TP-Link* (``fetch_image``) reads the page again, downloads that image's ``.zip`` (HTTPS from
  ``static.tp-link.com`` only, at most ``acs_firmware.MAX_IMAGE``), extracts its ``.bin`` and hands it to the ACS
  upgrade (``acs_firmware``). Nothing is installed without the operator.
- Versions compare by release number, then by build (``0.19.0 Build 260323`` > ``0.16.0 … Build 240530``).
"""
from __future__ import annotations

import html
import os
import re
import tempfile
import zipfile
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from app.models import Device, utcnow

SITE = "https://www.tp-link.com/it/support/download/"
STATIC_HOST = "static.tp-link.com"
REFRESH = timedelta(hours=24)
TIMEOUT = 30.0
USER_AGENT = "Mozilla/5.0 (compatible; NSM firmware catalog)"
TRANSPORT = None  # tests swap in an httpx.MockTransport
_last_refresh: datetime | None = None

_NAME = re.compile(r"^(?P<model>.+?)(?:\((?P<region>[^)]+)\))?_V(?P<hw>\d+(?:\.\d+)?)_(?P<version>.+)$", re.I)
_HW = re.compile(r"\bv(\d+)(?:\.(\d+))?\b", re.I)
_TABLE = re.compile(r'<table class="download-resource-table".*?</table>', re.S)
_ENTRY_NAME = re.compile(r'class="download-resource-name"[^>]*>\s*<p>([^<]+)</p>', re.S)
_ENTRY_LINK = re.compile(r'class="download-resource-btn[^"]*"[^>]*href="([^"]+)"', re.S)
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_BUILD = re.compile(r"build\s*(\d+)", re.I)
_DOTTED = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


class TplinkError(RuntimeError):
    pass


# --- Hardware and versions ---------------------------------------------------------------------

def _hw_key(major: str, minor: str | None) -> tuple[int, str]:
    return int(major), (minor or "").rstrip("0")


def hardware_key(text) -> tuple[int, str] | None:
    """``VX230v v1.0 00000000`` / ``1`` / ``3.20`` → comparable key ((1, ''), (3, '2'))."""
    text = str(text or "").strip()
    match = re.fullmatch(r"(\d+)(?:\.(\d+))?", text) or _HW.search(text)
    return _hw_key(match.group(1), match.group(2)) if match else None


def hardware_label(device) -> str | None:
    """Hardware version of a TP-Link device as the site names it (``V1``, ``V3.2``), from what the CPE reports."""
    key = hardware_key(((device.inventory_data or {}).get("tr069") or {}).get("hardware_version"))
    if key is None:
        return None
    return f"V{key[0]}" + (f".{key[1]}" if key[1] else "")


def version_key(value) -> tuple | None:
    """Release number then build: ``0.19.0 Build 260323`` → (0, 19, 0, 260323)."""
    text = str(value or "")
    dotted = _DOTTED.search(text)
    if not dotted:
        return None
    build = _BUILD.search(text)
    return tuple(int(p or 0) for p in dotted.groups()) + (int(build.group(1)) if build else 0,)


def slug(model: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "", re.sub(r"\s+", "-", str(model or "").strip().lower()))


# --- Site --------------------------------------------------------------------------------------

def _client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, transport=TRANSPORT, follow_redirects=False, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})


def _get(client, url: str) -> str | None:
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        raise TplinkError(f"Sito TP-Link non raggiungibile ({type(exc).__name__}).") from exc
    if response.status_code in (301, 302, 404):
        return None  # model or hardware version without a page on the Italian site
    if response.status_code != 200:
        raise TplinkError(f"Il sito TP-Link ha risposto HTTP {response.status_code}.")
    return response.text


def parse_page(text: str) -> list[dict]:
    """Firmware images of one download page: name, model, region, hardware key, version, date, link."""
    start = text.find('id="tabpanel-Firmware"')
    if start < 0:
        return []
    end = text.find('id="tabpanel-', start + 20)
    section = text[start:end if end > 0 else None]
    out = []
    for table in _TABLE.findall(section):
        name, link = _ENTRY_NAME.search(table), _ENTRY_LINK.search(table)
        if not name or not link:
            continue
        label = html.unescape(name.group(1)).strip()
        parsed = _NAME.match(label)
        if not parsed or version_key(parsed.group("version")) is None:
            continue
        stamp = _DATE.search(table)
        try:
            released = date.fromisoformat(stamp.group(1)) if stamp else None
        except ValueError:
            released = None
        out.append({"name": label, "model": parsed.group("model").strip(), "region": parsed.group("region"),
                    "hardware": hardware_key(parsed.group("hw")), "version": parsed.group("version").strip()[:80],
                    "released_at": released, "url": html.unescape(link.group(1)).strip()})
    return out


def _hardware_pages(text: str, model_slug: str) -> dict:
    """Links to the other hardware versions of the model (``…/<model>/v3.20/``) → hardware key."""
    pages = {}
    for link in set(re.findall(r'href="(https://www\.tp-link\.com/it/support/download/' + re.escape(model_slug) + r'/v[0-9.]+/)"', text)):
        key = hardware_key(link.rstrip("/").rsplit("/", 1)[-1])
        if key:
            pages[key] = link
    return pages


def images_for(client, model: str, hardware: tuple[int, str]) -> tuple[str | None, list[dict]]:
    """(page URL, images) of a model for one hardware version; images of other hardware versions are left out."""
    base = f"{SITE}{slug(model)}/"
    text = _get(client, base)
    if text is None:
        return None, []
    page = base
    other = _hardware_pages(text, slug(model)).get(hardware)
    if other and other != base:
        text = _get(client, other) or ""
        page = other
    return page, [image for image in parse_page(text) if image["hardware"] == hardware]


# --- Catalog -----------------------------------------------------------------------------------

def inventory_models(db) -> dict[tuple[str, tuple], str]:
    """(model, hardware key) of the TP-Link devices whose hardware version is known → model as the devices name it."""
    from app import vendor_cpe

    found = {}
    for device in db.scalars(select(Device).where(Device.model.is_not(None))):
        if vendor_cpe.brand(device) != "tp-link":
            continue
        key = hardware_key(((device.inventory_data or {}).get("tr069") or {}).get("hardware_version"))
        if key and slug(device.model):
            found[(slug(device.model), key)] = str(device.model).strip()
    return found


def refresh(db, now: datetime | None = None, force: bool = False) -> dict:
    """Record the images of the official site as catalog releases (once a day). Caller commits."""
    global _last_refresh
    from app import vendor_firmware

    now = now or utcnow()
    if not force and _last_refresh is not None and now - _last_refresh < REFRESH:
        return {"status": "not_due"}
    models = inventory_models(db)
    stats = {"status": "success", "models": len(models), "created": 0, "errors": []}
    if not models:
        return stats
    _last_refresh = now
    with _client() as client:
        for (_slug, hardware), model in sorted(models.items()):
            label = f"V{hardware[0]}" + (f".{hardware[1]}" if hardware[1] else "")
            try:
                page, images = images_for(client, model, hardware)
            except TplinkError as exc:
                stats["errors"].append(f"{model} {label}: {exc}")
                continue
            for image in images:
                stats["created"] += vendor_firmware._upsert(db, brand="tp-link", version=image["version"], source="vendor_site",
                                                            model_pattern=model.lower()[:120], platform=label, released_at=image["released_at"],
                                                            notes_url=page, now=now)
    db.flush()
    return stats


# --- Download ----------------------------------------------------------------------------------

def _safe_link(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname == STATIC_HOST and parts.path.lower().endswith(".zip")


def fetch_image(device, version: str, max_bytes: int) -> tuple[str, str, str]:
    """Download and extract the ``.bin`` of one release for this device: (path of the .bin, original name, zip URL).

    The caller removes the file. Raises ``TplinkError``.
    """
    hardware = hardware_key(((device.inventory_data or {}).get("tr069") or {}).get("hardware_version"))
    if not device.model or hardware is None:
        raise TplinkError("Modello o versione hardware del CPE non noti: carica il firmware a mano.")
    with _client() as client:
        _page, images = images_for(client, device.model, hardware)
        image = next((i for i in images if i["version"] == version), None)
        if image is None:
            raise TplinkError(f"La versione {version} non è più sul sito TP-Link per questo modello e versione hardware.")
        if not _safe_link(image["url"]):
            raise TplinkError("Il link del firmware non punta al server ufficiale TP-Link: scaricato nulla.")
        archive = tempfile.NamedTemporaryFile(prefix="nsm-tplink-", suffix=".zip", delete=False)
        try:
            with archive:
                try:
                    with client.stream("GET", image["url"], headers={"Accept": "application/octet-stream"}) as response:
                        if response.status_code != 200:
                            raise TplinkError(f"Il server TP-Link ha risposto HTTP {response.status_code} al download.")
                        size = 0
                        for chunk in response.iter_bytes(1024 * 1024):
                            size += len(chunk)
                            if size > max_bytes:
                                raise TplinkError("Il firmware supera la dimensione massima accettata.")
                            archive.write(chunk)
                except httpx.HTTPError as exc:
                    raise TplinkError(f"Download dal sito TP-Link non riuscito ({type(exc).__name__}).") from exc
            return _extract_bin(archive.name, max_bytes) + (image["url"],)
        finally:
            os.unlink(archive.name)


def _extract_bin(path: str, max_bytes: int) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            bins = [i for i in archive.infolist() if not i.is_dir() and i.filename.lower().endswith(".bin")]
            if len(bins) != 1:
                raise TplinkError(f"L'archivio TP-Link contiene {len(bins)} file .bin invece di uno: carica il firmware a mano.")
            entry = bins[0]
            if entry.file_size > max_bytes:
                raise TplinkError("Il firmware supera la dimensione massima accettata.")
            out = tempfile.NamedTemporaryFile(prefix="nsm-tplink-", suffix=".bin", delete=False)
            with out, archive.open(entry) as source:
                written = 0
                while chunk := source.read(1024 * 1024):
                    written += len(chunk)
                    if written > max_bytes:  # the size in the archive header can lie
                        raise TplinkError("Il firmware supera la dimensione massima accettata.")
                    out.write(chunk)
            return out.name, os.path.basename(entry.filename)
    except zipfile.BadZipFile as exc:
        raise TplinkError("Il file scaricato da TP-Link non è un archivio valido.") from exc
