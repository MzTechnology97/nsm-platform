import uuid

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import main as core
from app.agent_models import DeviceAgentCredential
from app.agent_service import (
    AGENT_VERSION,
    derive_device_secret,
    record_status_event,
    upsert_agent_credential,
    verify_agent_secret,
)
from app.db import SessionLocal
from app.models import AuditEvent, Device, utcnow
from app.security import validate_csrf

router = APIRouter()
MAX_AGENT_BODY = 64 * 1024


def _parse_line_body(raw: bytes) -> dict[str, str]:
    text = raw.decode("utf-8", errors="replace")
    text = text.replace("\\n", "\n")
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        if key:
            values[key] = value.strip()
    return values


def _opt_int(value: str | None):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _inventory_from_values(values: dict[str, str]):
    return {
        "identity": core.opt(values.get("identity")),
        "model": core.opt(values.get("model")),
        "version": core.opt(values.get("version")),
        "architecture": core.opt(values.get("architecture")),
        "serial": core.opt(values.get("serial")),
        "software_id": core.opt(values.get("software_id")),
        "routerboot": core.opt(values.get("routerboot")),
    }


def _apply_inventory(device: Device, values: dict[str, str]):
    inventory = _inventory_from_values(values)
    before = {
        "identity": device.device_identity,
        "model": device.model,
        "firmware": device.firmware_version,
        "architecture": device.architecture,
        "serial": device.serial_number,
        "software_id": device.software_id,
        "routerboot": device.routerboot_version,
    }
    if inventory["identity"]:
        device.device_identity = inventory["identity"]
    if inventory["model"]:
        device.model = inventory["model"]
    if inventory["version"]:
        device.firmware_version = inventory["version"]
    if inventory["architecture"]:
        device.architecture = inventory["architecture"]
    if inventory["serial"]:
        device.serial_number = inventory["serial"]
    if inventory["software_id"]:
        device.software_id = inventory["software_id"]
    if inventory["routerboot"]:
        device.routerboot_version = inventory["routerboot"]
    after = {
        "identity": device.device_identity,
        "model": device.model,
        "firmware": device.firmware_version,
        "architecture": device.architecture,
        "serial": device.serial_number,
        "software_id": device.software_id,
        "routerboot": device.routerboot_version,
    }
    return before, after


def _agent_script(device_id: uuid.UUID, secret: str, heartbeat_url: str) -> str:
    return f'''/system script add name="nsm-agent" policy=read,test source={{
    :local nsmDeviceId "{device_id}"
    :local nsmSecret "{secret}"
    :local nsmHeartbeat "{heartbeat_url}"
    :local nsmIdentity [/system identity get name]
    :local nsmModel [/system resource get board-name]
    :local nsmVersion [/system resource get version]
    :local nsmArch [/system resource get architecture-name]
    :local nsmUptime [/system resource get uptime]
    :local nsmCpu [/system resource get cpu-load]
    :local nsmFreeMemory [/system resource get free-memory]
    :local nsmTotalMemory [/system resource get total-memory]
    :local nsmSerial ""
    :local nsmRouterboot ""
    :do {{ :set nsmSerial [/system routerboard get serial-number] }} on-error={{}}
    :do {{ :set nsmRouterboot [/system routerboard get current-firmware] }} on-error={{}}
    :local nsmBody ("device_id=" . $nsmDeviceId . "\\ndevice_secret=" . $nsmSecret . "\\nidentity=" . $nsmIdentity . "\\nmodel=" . $nsmModel . "\\nversion=" . $nsmVersion . "\\narchitecture=" . $nsmArch . "\\nserial=" . $nsmSerial . "\\nrouterboot=" . $nsmRouterboot . "\\nuptime=" . $nsmUptime . "\\ncpu_load=" . $nsmCpu . "\\nfree_memory=" . $nsmFreeMemory . "\\ntotal_memory=" . $nsmTotalMemory)
    /tool fetch url=$nsmHeartbeat http-method=post http-header-field="Content-Type: text/plain" http-data=$nsmBody keep-result=no
}}'''


def _bootstrap_script(device: Device, raw_token: str, base_url: str) -> str:
    secret = derive_device_secret(raw_token, device.id)
    complete_url = f"{base_url}/api/v1/agent/mikrotik/complete"
    heartbeat_url = f"{base_url}/api/v1/agent/mikrotik/heartbeat"
    agent_script = _agent_script(device.id, secret, heartbeat_url)
    return f''':local nsmToken "{raw_token}"
:local nsmComplete "{complete_url}"
:local nsmIdentity [/system identity get name]
:local nsmModel [/system resource get board-name]
:local nsmVersion [/system resource get version]
:local nsmArch [/system resource get architecture-name]
:local nsmSerial ""
:local nsmSoftwareId ""
:local nsmRouterboot ""
:do {{ :set nsmSerial [/system routerboard get serial-number] }} on-error={{}}
:do {{ :set nsmSoftwareId [/system license get software-id] }} on-error={{}}
:do {{ :set nsmRouterboot [/system routerboard get current-firmware] }} on-error={{}}
:local nsmBody ("token=" . $nsmToken . "\\nidentity=" . $nsmIdentity . "\\nmodel=" . $nsmModel . "\\nversion=" . $nsmVersion . "\\narchitecture=" . $nsmArch . "\\nserial=" . $nsmSerial . "\\nsoftware_id=" . $nsmSoftwareId . "\\nrouterboot=" . $nsmRouterboot)
/tool fetch url=$nsmComplete http-method=post http-header-field="Content-Type: text/plain" http-data=$nsmBody keep-result=no
:do {{ /system scheduler remove [find where name="nsm-agent"] }} on-error={{}}
:do {{ /system script remove [find where name="nsm-agent"] }} on-error={{}}
{agent_script}
/system scheduler add name="nsm-agent" interval=5m start-time=startup on-event="/system script run nsm-agent" policy=read,test
/system script run nsm-agent
:log info "NSM outbound agent installed"
:do {{ /file remove "nsm-agent-bootstrap.rsc" }} on-error={{}}
'''


@router.get("/devices/{device_id}/agent", response_class=HTMLResponse)
def agent_page(request: Request, device_id: uuid.UUID):
    with SessionLocal() as db:
        user = core.current_user(request, db)
        if not user:
            return core.login_redirect()
        if not core.has_permission(user, "devices.read"):
            raise HTTPException(403)
        device = db.scalar(
            select(Device)
            .where(Device.id == device_id)
            .options(selectinload(Device.customer), selectinload(Device.site))
        )
        if not device:
            raise HTTPException(404)
        if device.vendor != "mikrotik":
            raise HTTPException(400, "Agent outbound disponibile solo per MikroTik.")
        credential = db.get(DeviceAgentCredential, device.id)
        raw_token = request.session.pop(f"agent_enrollment_token:{device.id}", None)
        enrollment_command = None
        if raw_token:
            base_url = str(request.base_url).rstrip("/")
            enrollment_command = (
                f'/tool fetch url="{base_url}/api/v1/agent/mikrotik/bootstrap?token={raw_token}" '
                f'dst-path="nsm-agent-bootstrap.rsc"; '
                f'/import file-name="nsm-agent-bootstrap.rsc"'
            )
        health = (device.inventory_data or {}).get("health", {})
        return core.render(
            request,
            db,
            user,
            "mikrotik_agent.html",
            device=device,
            credential=credential,
            health=health,
            enrollment_command=enrollment_command,
            transport_secure=str(request.base_url).lower().startswith("https://"),
            agent_version=AGENT_VERSION,
        )


@router.post("/devices/{device_id}/agent/enrollment")
def create_agent_enrollment(
    request: Request, device_id: uuid.UUID, csrf: str = Form(...)
):
    validate_csrf(request, csrf)
    with SessionLocal() as db:
        user = core.require_permission(request, db, "devices.enroll")
        device = db.get(Device, device_id)
        if not device:
            raise HTTPException(404)
        if device.vendor != "mikrotik":
            raise HTTPException(400, "Agent outbound disponibile solo per MikroTik.")
        raw_token, _ = core.create_enrollment(db, device, user, source="mikrotik_agent_v1")
        device.status = "pending_enrollment"
        db.commit()
        request.session[f"agent_enrollment_token:{device.id}"] = raw_token
    return RedirectResponse(f"/devices/{device_id}/agent", status_code=303)


@router.get("/api/v1/agent/mikrotik/bootstrap", response_class=PlainTextResponse)
def agent_bootstrap(request: Request, token: str):
    with SessionLocal() as db:
        enrollment = core.get_valid_enrollment(db, token)
        if not enrollment:
            raise HTTPException(401, "Enrollment token non valido o scaduto.")
        device = db.get(Device, enrollment.device_id)
        if not device or device.vendor != "mikrotik":
            raise HTTPException(400, "Device non valido per agent MikroTik.")
    base_url = str(request.base_url).rstrip("/")
    return PlainTextResponse(
        _bootstrap_script(device, token, base_url),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/api/v1/agent/mikrotik/complete")
async def agent_complete(request: Request):
    raw = await request.body()
    if len(raw) > MAX_AGENT_BODY:
        raise HTTPException(413, "Payload troppo grande.")
    values = _parse_line_body(raw)
    raw_token = values.get("token", "")
    if not raw_token:
        raise HTTPException(400, "Token mancante.")

    with SessionLocal() as db:
        enrollment = core.get_valid_enrollment(db, raw_token)
        if not enrollment:
            raise HTTPException(401, "Enrollment token non valido o scaduto.")
        device = db.get(Device, enrollment.device_id)
        if not device or device.vendor != "mikrotik":
            raise HTTPException(400, "Device non valido.")

        secret = derive_device_secret(raw_token, device.id)
        source_ip = request.client.host if request.client else None
        credential = upsert_agent_credential(db, device.id, secret, source_ip)
        before, after = _apply_inventory(device, values)
        previous_status = device.status
        now = utcnow()
        device.management_source = "mikrotik_agent"
        device.inventory_source = "mikrotik_agent"
        device.inventory_last_verified_at = now
        device.last_seen = now
        device.status = "online"
        device.inventory_data = {
            **(device.inventory_data or {}),
            "agent": {"version": AGENT_VERSION, "source_ip": source_ip},
        }
        credential.last_heartbeat_at = now
        enrollment.status = "used"
        enrollment.used_at = now

        db.add(
            AuditEvent(
                event_type="DEVICE_AGENT_ENROLLED",
                customer_id=device.customer_id,
                device_id=device.id,
                source="mikrotik_agent",
                result="success",
                details={
                    "agent_version": AGENT_VERSION,
                    "source_ip": source_ip,
                    "previous_status": previous_status,
                },
            )
        )
        if before != after:
            db.add(
                AuditEvent(
                    event_type="INVENTORY_DISCOVERED",
                    customer_id=device.customer_id,
                    device_id=device.id,
                    source="mikrotik_agent",
                    result="success",
                    details={"before": before, "after": after},
                )
            )
        db.commit()
        return {
            "status": "ok",
            "device_id": str(device.id),
            "agent_version": AGENT_VERSION,
        }


@router.post("/api/v1/agent/mikrotik/heartbeat")
async def agent_heartbeat(request: Request):
    raw = await request.body()
    if len(raw) > MAX_AGENT_BODY:
        raise HTTPException(413, "Payload troppo grande.")
    values = _parse_line_body(raw)
    try:
        device_id = uuid.UUID(values.get("device_id", ""))
    except ValueError:
        raise HTTPException(400, "Device ID non valido.")
    candidate_secret = values.get("device_secret", "")

    with SessionLocal() as db:
        credential = db.get(DeviceAgentCredential, device_id)
        if not credential or not verify_agent_secret(credential, candidate_secret):
            raise HTTPException(401, "Credenziale agent non valida.")
        device = db.get(Device, device_id)
        if not device or device.vendor != "mikrotik":
            raise HTTPException(404, "Device non trovato.")

        previous_status = device.status
        previous_identity = device.device_identity
        previous_firmware = device.firmware_version
        before, after = _apply_inventory(device, values)
        now = utcnow()
        source_ip = request.client.host if request.client else None
        health = {
            "uptime": core.opt(values.get("uptime")),
            "cpu_load": _opt_int(values.get("cpu_load")),
            "free_memory": _opt_int(values.get("free_memory")),
            "total_memory": _opt_int(values.get("total_memory")),
            "observed_at": now.isoformat(),
        }
        device.inventory_data = {
            **(device.inventory_data or {}),
            "health": health,
            "agent": {"version": credential.agent_version, "source_ip": source_ip},
        }
        device.inventory_source = "mikrotik_agent"
        device.inventory_last_verified_at = now
        device.management_source = "mikrotik_agent"
        device.last_seen = now
        device.status = "online"
        credential.last_heartbeat_at = now
        credential.last_source_ip = source_ip

        if previous_status != "online":
            record_status_event(
                db,
                device,
                "DEVICE_ONLINE",
                {"previous_status": previous_status, "source_ip": source_ip},
            )
        if previous_identity and device.device_identity != previous_identity:
            record_status_event(
                db,
                device,
                "DEVICE_IDENTITY_CHANGED",
                {"before": previous_identity, "after": device.device_identity},
            )
        if previous_firmware and device.firmware_version != previous_firmware:
            record_status_event(
                db,
                device,
                "FIRMWARE_CHANGED",
                {"before": previous_firmware, "after": device.firmware_version},
            )
        if before != after:
            device.inventory_last_verified_at = now
        db.commit()
        return JSONResponse({"status": "ok", "server_time": now.isoformat()})


def install_mikrotik_agent(app):
    app.include_router(router)
