import ipaddress
from datetime import timedelta

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import main as core
from app.agent_models import DeviceJob
from app.models import Device, utcnow

router = APIRouter()
SNAPSHOT_SECTIONS = {
    "resources": "Risorse di sistema",
    "ip_addresses": "Indirizzi IP",
    "routes": "Route IP",
    "interfaces": "Interfacce",
    "firewall": "Firewall",
    "ppp_active": "Sessioni PPP",
    "dhcp_leases": "DHCP leases",
    "logs": "Warning / error log",
    "services": "Servizi esposti",
    "queues": "Code (queue)",
    "certificates": "Certificati",
    "routing": "BGP / OSPF",
}
DIAGNOSTIC_TYPES = {
    "ping": "diagnostic_ping",
    "traceroute": "diagnostic_traceroute",
    "neighbors": "diagnostic_neighbors",
    "dhcp_lookup": "diagnostic_dhcp_lookup",
    "logs": "diagnostic_logs",
    "support_snapshot": "support_snapshot",
}


def _load_device(db, device_id):
    device = db.scalar(select(Device).where(Device.id == device_id).options(selectinload(Device.customer), selectinload(Device.site)))
    if not device:
        raise HTTPException(404)
    return device


def _latest_snapshots(db, device_id):
    rows = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type == "snapshot_section", DeviceJob.status == "success").order_by(DeviceJob.completed_at.desc().nullslast(), DeviceJob.created_at.desc()).limit(80)))
    latest = {}
    for row in rows:
        section = str((row.payload or {}).get("section") or "")
        if section and section not in latest:
            latest[section] = row
    return latest


def _pending_sections(db, device_id):
    rows = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == device_id, DeviceJob.job_type == "snapshot_section", DeviceJob.status.in_(["pending", "delivered"]))))
    return {str((r.payload or {}).get("section") or "") for r in rows}


def queue_snapshot_record(db, user, device, section: str) -> bool:
    """Ask the agent for a fresh read of one configuration section (audited) and commit.

    Returns False when the same section is already queued; ``InputError`` ``section_invalid`` (400),
    ``not_online`` (409, the device must be an online MikroTik) or ``section_unsupported`` (409, the installed
    Agent cannot read the section: ``mikrotik_snapshot_capabilities``).
    """
    from app.input_errors import InputError

    if section not in SNAPSHOT_SECTIONS:
        raise InputError("section_invalid", "Sezione snapshot non valida.")
    if device.vendor != "mikrotik" or device.status != "online":
        raise InputError("not_online", "Lo snapshot richiede un MikroTik online con agent NSM.", 409)
    from app.mikrotik_snapshot_capabilities import section_support, unsupported_message

    support = section_support(device, section)
    if not support["supported"]:
        raise InputError("section_unsupported", unsupported_message(support), 409)
    pending = list(db.scalars(select(DeviceJob).where(DeviceJob.device_id == device.id, DeviceJob.job_type == "snapshot_section", DeviceJob.status.in_(["pending", "delivered"]))))
    if any((job.payload or {}).get("section") == section for job in pending):
        return False
    job = DeviceJob(device_id=device.id, job_type="snapshot_section", payload={"section": section}, expires_at=utcnow() + timedelta(minutes=10))
    db.add(job)
    db.flush()
    core.add_event(db, "DEVICE_SNAPSHOT_QUEUED", actor=user, customer_id=device.customer_id, device_id=device.id, details={"section": section, "job_id": str(job.id)}, source="portal")
    db.commit()
    return True


def _safe_target(value: str) -> str:
    value = (value or "").strip()
    if not value or len(value) > 253 or any(ch in value for ch in " ;|&$`\\\n\r\t"):
        raise HTTPException(400, "Target diagnostico non valido.")
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        labels = value.rstrip(".").split(".")
        if not labels or any(not label or len(label) > 63 or not all(c.isalnum() or c == "-" for c in label) or label[0] == "-" or label[-1] == "-" for label in labels):
            raise HTTPException(400, "Target diagnostico non valido.")
        return value


def _safe_source(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise HTTPException(400, "IP sorgente non valido.")
    return str(address)


def _safe_dhcp_query(value: str):
    value = (value or "").strip()
    if not value or len(value) > 64:
        raise HTTPException(400, "Ricerca DHCP non valida.")
    try:
        address = ipaddress.ip_address(value)
        if address.version != 4:
            raise HTTPException(400, "La ricerca DHCP supporta indirizzi IPv4 o MAC.")
        return str(address), "ip"
    except HTTPException:
        raise
    except ValueError:
        try:
            return core.norm_mac(value), "mac"
        except ValueError:
            raise HTTPException(400, "La ricerca DHCP richiede un indirizzo IPv4 o MAC valido.")


def queue_diagnostic_record(db, user, device, diagnostic: str, target: str = "", source: str = "", query: str = "") -> DeviceJob:
    """Queue one diagnostic (ping, traceroute, neighbors, DHCP lookup, logs, support snapshot) on an online MikroTik, audited; commits.

    ``HTTPException`` 400 for an unsupported diagnostic or an invalid target, source or DHCP query; 409 when the device is
    not an online MikroTik.
    """
    if diagnostic not in DIAGNOSTIC_TYPES:
        raise HTTPException(400, "Diagnostica non supportata.")
    payload = {}
    details = {"diagnostic": diagnostic}
    if diagnostic in {"ping", "traceroute"}:
        payload["target"] = _safe_target(target)
        payload["source"] = _safe_source(source)
        details.update({"target": payload["target"], "source": payload["source"] or None})
    elif diagnostic == "dhcp_lookup":
        normalized, lookup_type = _safe_dhcp_query(query)
        payload.update({"query": normalized, "lookup_type": lookup_type})
        details.update({"query": normalized, "lookup_type": lookup_type})
    if device.vendor != "mikrotik" or device.status != "online":
        raise HTTPException(409, "La diagnostica richiede un MikroTik online con agent NSM.")
    job_type = DIAGNOSTIC_TYPES[diagnostic]
    expiry = timedelta(minutes=10 if diagnostic == "support_snapshot" else 5)
    job = DeviceJob(device_id=device.id, job_type=job_type, payload=payload, expires_at=utcnow() + expiry)
    db.add(job)
    db.flush()
    details["job_id"] = str(job.id)
    core.add_event(db, "DEVICE_DIAGNOSTIC_QUEUED", actor=user, customer_id=device.customer_id, device_id=device.id, details=details, source="portal")
    db.commit()
    return job


def install_mikrotik_workspace(app):
    app.include_router(router)
