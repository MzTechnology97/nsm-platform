"""Vendor profiles for CVE correlation beyond MikroTik (SEC-05).

Maps an NSM Device to the CPE vendor names and products NVD uses for it, and
compares firmware versions.  NVD names firmware as ``<model>_firmware`` (for
example ``tp-link:archer_c6_firmware``, ``cambiumnetworks:epmp_1000_firmware``,
``mimosa:b5_firmware``) plus a few family products (Ubiquiti ``airos``,
``airmax_ac_firmware``…).  A Device is only evaluated when its brand, model
and firmware version are known; nothing is inferred from the brand alone.
"""
from __future__ import annotations

import re

from app.routeros_version import compare_routeros_versions

# brand key → label and CPE vendor names used by NVD
BRANDS = {
    "mikrotik": {"label": "MikroTik", "cpe_vendors": ("mikrotik",)},
    "ubiquiti": {"label": "Ubiquiti", "cpe_vendors": ("ui", "ubnt", "ubiquiti")},
    "tp-link": {"label": "TP-Link", "cpe_vendors": ("tp-link",)},
    "cambium": {"label": "Cambium Networks", "cpe_vendors": ("cambiumnetworks",)},
    "mimosa": {"label": "Mimosa", "cpe_vendors": ("mimosa",)},
    "tenda": {"label": "Tenda", "cpe_vendors": ("tenda",)},
    "huawei": {"label": "Huawei", "cpe_vendors": ("huawei",)},
    "zyxel": {"label": "Zyxel", "cpe_vendors": ("zyxel",)},
    "d-link": {"label": "D-Link", "cpe_vendors": ("dlink",)},
    "netgear": {"label": "Netgear", "cpe_vendors": ("netgear",)},
    # Wireless / WISP
    "siklu": {"label": "Siklu", "cpe_vendors": ("siklu",)},
    "ceragon": {"label": "Ceragon", "cpe_vendors": ("ceragon",)},
    "ruckus": {"label": "Ruckus (CommScope)", "cpe_vendors": ("ruckuswireless", "commscope")},
    "teltonika": {"label": "Teltonika", "cpe_vendors": ("teltonika", "teltonika-networks")},
    "peplink": {"label": "Peplink", "cpe_vendors": ("peplink",)},
    "ruijie": {"label": "Ruijie / Reyee", "cpe_vendors": ("ruijie", "ruijienetworks")},
    # CPE, fibre and residential gateways
    "zte": {"label": "ZTE", "cpe_vendors": ("zte",)},
    "fiberhome": {"label": "FiberHome", "cpe_vendors": ("fiberhome",)},
    "avm": {"label": "AVM FRITZ!Box", "cpe_vendors": ("avm",), "families": ("fritz!_os",)},
    "draytek": {"label": "DrayTek", "cpe_vendors": ("draytek",)},
    "asus": {"label": "ASUS", "cpe_vendors": ("asus",)},
    "linksys": {"label": "Linksys", "cpe_vendors": ("linksys",)},
    "totolink": {"label": "TOTOLINK", "cpe_vendors": ("totolink",)},
    "cudy": {"label": "Cudy", "cpe_vendors": ("cudy",)},
    "grandstream": {"label": "Grandstream", "cpe_vendors": ("grandstream",)},
    # Enterprise networking and security
    "cisco": {"label": "Cisco", "cpe_vendors": ("cisco",)},
    "juniper": {"label": "Juniper", "cpe_vendors": ("juniper",), "families": ("junos",)},
    "fortinet": {"label": "Fortinet", "cpe_vendors": ("fortinet",), "families": ("fortios",)},
    "paloalto": {"label": "Palo Alto Networks", "cpe_vendors": ("paloaltonetworks",), "families": ("pan-os",)},
    "sonicwall": {"label": "SonicWall", "cpe_vendors": ("sonicwall",), "families": ("sonicos",)},
    "aruba": {"label": "Aruba (HPE)", "cpe_vendors": ("arubanetworks",), "families": ("arubaos",)},
    "sophos": {"label": "Sophos", "cpe_vendors": ("sophos",)},
}
# Brand icons in static/brand-icons.svg (Simple Icons, CC0); other brands use the generic device icon.
ICONS = {
    "mikrotik": "#293239", "ubiquiti": "#0559C9", "tp-link": "#4ACBD6", "huawei": "#FF0000", "fortinet": "#EE3124",
    "juniper": "#84B135", "cisco": "#1BA0D7", "paloalto": "#F04E23", "sonicwall": "#FF791A", "netgear": "#2C262D",
    "asus": "#000000", "linksys": "#000000", "avm": "#E2001A",
}
# Manufacturers selectable for manually added ("generic") devices.
MANUAL_BRANDS = tuple(key for key in BRANDS if key not in ("mikrotik", "ubiquiti"))
_DOTTED = re.compile(r"(\d+(?:\.\d+)+)")


def brand(device) -> str | None:
    vendor = (device.vendor or "").strip().lower()
    if vendor in BRANDS:
        return vendor
    if vendor == "generic":
        manufacturer = str(((device.inventory_data or {}).get("manufacturer") or "")).strip().lower()
        return manufacturer if manufacturer in BRANDS else None
    return None


def slug(model: str | None) -> str | None:
    text = re.sub(r"\s+", "_", str(model or "").strip().lower())
    text = re.sub(r"[^a-z0-9_.-]", "", text)
    return text or None


def _ubiquiti_families(model_slug: str) -> list[str]:
    products = []
    if re.search(r"(^|[-_])(er|erlite|erpoe|erpro)[-_]", model_slug + "_") or model_slug.startswith("edgerouter"):
        products += ["edgeos", "edgemax_edgerouter_firmware"]
    elif model_slug.startswith(("es-", "edgeswitch")):
        products += ["edgeswitch_firmware"]
    elif model_slug.startswith(("af", "airfiber")):
        products += []
    else:
        # airMAX radios (LiteBeam, NanoBeam, NanoStation, PowerBeam, Rocket, …) run airOS.
        products.append("airos")
        if re.search(r"ac|prism|gen2|5ac|2ac", model_slug):
            products.append("airmax_ac_firmware")
        elif re.search(r"(^|[-_])(m2|m3|m5|m365|m900|xm|xw|ti)([-_]|$)|_m[0-9]", model_slug):
            products.append("airmax_m_firmware")
    return products


def products(device) -> tuple[str, ...]:
    """CPE products that may describe the Device firmware (most specific first)."""
    key = brand(device)
    if not key:
        return ()
    if key == "mikrotik":
        return ("routeros",)
    model_slug = slug(device.model)
    # Single-OS vendors (FortiOS, Junos, PAN-OS…) can be evaluated from the OS version even without a model.
    candidates = [f"{model_slug}_firmware"] if model_slug else []
    if key == "ubiquiti" and model_slug:
        candidates += _ubiquiti_families(model_slug)
    candidates += list(BRANDS[key].get("families", ()))
    return tuple(dict.fromkeys(candidates))


def cpe_vendors(device) -> tuple[str, ...]:
    key = brand(device)
    return BRANDS[key]["cpe_vendors"] if key else ()


def version_tuple(value) -> tuple[int, ...] | None:
    match = _DOTTED.search(str(value or ""))
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def normalize_version(device_brand: str | None, value) -> str | None:
    if device_brand == "mikrotik":
        return str(value).split(" ")[0].strip() if value else None
    parsed = version_tuple(value)
    return ".".join(str(p) for p in parsed) if parsed else None


def compare(device_brand: str | None, left, right) -> int | None:
    """-1 / 0 / 1, or None when one side cannot be interpreted."""
    if device_brand == "mikrotik":
        return compare_routeros_versions(left, right)
    a, b = version_tuple(left), version_tuple(right)
    if a is None or b is None:
        return None
    width = max(len(a), len(b))
    a, b = a + (0,) * (width - len(a)), b + (0,) * (width - len(b))
    return (a > b) - (a < b)


def parseable(device_brand: str | None, value) -> bool:
    if device_brand == "mikrotik":
        from app.routeros_version import parse_routeros_version

        return parse_routeros_version(value) is not None
    return version_tuple(value) is not None


def tracked_cpes(devices, limit: int = 60) -> list[str]:
    """NVD ``virtualMatchString`` prefixes for the brands and models actually in the inventory."""
    cpes = ["cpe:2.3:o:mikrotik:routeros:*:*:*:*:*:*:*:*"]
    for device in devices:
        for vendor in cpe_vendors(device):
            for product in products(device):
                if (vendor, product) == ("mikrotik", "routeros"):
                    continue
                cpes.append(f"cpe:2.3:o:{vendor}:" + product.replace("!", "\\!"))
    return list(dict.fromkeys(cpes))[:limit]


def icon(device) -> dict:
    """Symbol, colour and label of the manufacturer icon for one Device."""
    key = brand(device)
    if key in ICONS:
        return {"symbol": f"brand-{key}", "color": ICONS[key], "label": label(key), "brand": True}
    return {"symbol": "brand-generic", "color": None, "label": label(key) if key else "Produttore non indicato", "brand": False}


def icon_svg(device, size: int = 20):
    """Inline SVG referencing the icon sprite (CSP-safe: no style attribute)."""
    from markupsafe import Markup, escape

    data = icon(device)
    fill = f' fill="{data["color"]}"' if data["color"] else ""
    kind = "brand" if data["brand"] else "generic"
    return Markup(f'<span class="brand-icon brand-icon-{kind}" title="{escape(data["label"])}"><svg width="{size}" height="{size}" viewBox="0 0 24 24"{fill} aria-hidden="true">'
                  f'<use href="/static/brand-icons.svg#{data["symbol"]}"></use></svg></span>')


def label(device_brand: str | None) -> str:
    return BRANDS.get(device_brand or "", {}).get("label", device_brand or "—")


def evaluable(device) -> bool:
    """Brand, model and a readable firmware version are known: CVE correlation can decide."""
    key = brand(device)
    return bool(key and products(device) and device.firmware_version and parseable(key, device.firmware_version))


def coverage(devices) -> list[dict]:
    """Per brand in the inventory: devices, how many can be evaluated and why the others cannot."""
    rows = {}
    for device in devices:
        key = brand(device)
        if not key:
            continue
        row = rows.setdefault(key, {"brand": key, "label": label(key), "devices": 0, "evaluable": 0, "no_model": 0, "no_version": 0, "products": set()})
        row["devices"] += 1
        found = products(device)
        version = device.firmware_version
        if not found:
            row["no_model"] += 1
        elif not version or not parseable(key, version):
            row["no_version"] += 1
        else:
            row["evaluable"] += 1
            row["products"].update(found)
    out = []
    for row in sorted(rows.values(), key=lambda r: -r["devices"]):
        row["products"] = sorted(row["products"])[:8]
        out.append(row)
    return out


def install_vendor_cpe() -> None:
    from sqlalchemy import select

    from app import main as core
    from app.db import SessionLocal
    from app.models import Device

    def _coverage():
        with SessionLocal() as db:
            return coverage(db.scalars(select(Device)))

    core.templates.env.globals.update(manual_brands=MANUAL_BRANDS, brand_label=label, cve_coverage=_coverage, cve_evaluable=evaluable, brand_icon=icon_svg,
                                      device_brand_label=lambda device: label(brand(device)) if brand(device) else None)
