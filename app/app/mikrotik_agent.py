import hashlib
import json
import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app import main as core
from app.agent_models import DeviceAgentCredential, DeviceJob
from app.db import SessionLocal
from app.models import Device, DeviceEnrollment, utcnow

router = APIRouter()
AGENT_VERSION = "0.7.0"
MAX_AGENT_BODY = 64 * 1024
HEARTBEAT_INTERVAL_SECONDS = 120


def _remove_route(app, path: str, method: str):
    method = method.upper()
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and method in (getattr(route, "methods", set()) or set())
        )
    ]


def _secret_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def _json_body(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > MAX_AGENT_BODY:
        raise HTTPException(413, "Payload agent troppo grande.")
    try:
        payload = json.loads(raw or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(400, "JSON non valido.")
    if not isinstance(payload, dict):
        raise HTTPException(400, "Payload JSON non valido.")
    return payload


def _string(value, limit=1000):
    if value is None:
        return None
    value = str(value).strip()
    return value[:limit] if value else None


def _normalize_mac(value):
    try:
        return core.norm_mac(_string(value, 64))
    except ValueError:
        return None


def _inventory_snapshot(device: Device):
    return {
        "identity": device.device_identity,
        "model": device.model,
        "serial": device.serial_number,
        "primary_mac": device.primary_mac,
        "routeros_version": device.firmware_version,
        "architecture": device.architecture,
        "software_id": device.software_id,
        "routerboot_version": device.routerboot_version,
    }


def _apply_inventory(db, device: Device, inventory: dict, request: Request, source: str):
    before = _inventory_snapshot(device)
    observed_mac = _normalize_mac(inventory.get("primary_mac"))
    if observed_mac:
        duplicate = db.scalar(
            select(Device.id).where(
                Device.vendor == "mikrotik",
                Device.primary_mac == observed_mac,
                Device.id != device.id,
            )
        )
        if duplicate:
            raise HTTPException(409, "Il MAC rilevato è già associato a un altro MikroTik.")

    device.device_identity = _string(inventory.get("identity"), 200) or device.device_identity
    device.model = _string(inventory.get("model") or inventory.get("board_name"), 150) or device.model
    device.serial_number = _string(inventory.get("serial") or inventory.get("serial_number"), 150) or device.serial_number
    device.primary_mac = observed_mac or device.primary_mac
    device.firmware_version = _string(inventory.get("routeros_version") or inventory.get("version"), 150) or device.firmware_version
    device.architecture = _string(inventory.get("architecture"), 100) or device.architecture
    device.software_id = _string(inventory.get("software_id"), 100) or device.software_id
    device.routerboot_version = _string(
        inventory.get("routerboot_version") or inventory.get("routerboot"), 100
    ) or device.routerboot_version
    device.inventory_source = source
    device.inventory_last_verified_at = utcnow()
    device.management_source = "mikrotik_agent"
    device.status = "online"
    device.last_seen = utcnow()

    source_ip = request.client.host if request.client else None
    current_data = dict(device.inventory_data or {})
    current_data.update(
        {
            "agent_version": _string(inventory.get("agent_version"), 40) or AGENT_VERSION,
            "last_source_ip": source_ip,
            "uptime": _string(inventory.get("uptime"), 80),
            "cpu": _string(inventory.get("cpu"), 100),
            "cpu_count": _string(inventory.get("cpu_count"), 20),
            "total_memory": _string(inventory.get("total_memory"), 80),
            "free_memory": _string(inventory.get("free_memory"), 80),
            "packages": inventory.get("packages") if isinstance(inventory.get("packages"), list) else current_data.get("packages", []),
            "interfaces": inventory.get("interfaces") if isinstance(inventory.get("interfaces"), list) else current_data.get("interfaces", []),
        }
    )
    device.inventory_data = current_data

    after = _inventory_snapshot(device)
    changes = {}
    event_map = {
        "identity": "DEVICE_IDENTITY_CHANGED",
        "model": "MODEL_DISCOVERED",
        "serial": "SERIAL_DISCOVERED",
        "primary_mac": "MAC_DISCOVERED",
        "routeros_version": "FIRMWARE_CHANGED",
    }
    for key, event_type in event_map.items():
        if before.get(key) != after.get(key):
            changes[key] = {"before": before.get(key), "after": after.get(key)}
            core.add_event(
                db,
                event_type,
                customer_id=device.customer_id,
                device_id=device.id,
                details=changes[key],
                source=source,
            )
    return before, after, changes


def _authenticate_agent(db, request: Request):
    raw_device_id = request.headers.get("X-NSM-Device-ID", "").strip()
    raw_secret = request.headers.get("X-NSM-Device-Secret", "").strip()
    if not raw_device_id or not raw_secret:
        raise HTTPException(401, "Credenziali agent mancanti.")
    try:
        device_id = uuid.UUID(raw_device_id)
    except ValueError:
        raise HTTPException(401, "Credenziali agent non valide.")
    credential = db.scalar(
        select(DeviceAgentCredential).where(
            DeviceAgentCredential.device_id == device_id,
            DeviceAgentCredential.agent_type == "mikrotik_agent",
            DeviceAgentCredential.is_active.is_(True),
        )
    )
    if not credential or not secrets.compare_digest(
        credential.secret_hash, _secret_digest(raw_secret)
    ):
        raise HTTPException(401, "Credenziali agent non valide.")
    device = db.get(Device, device_id)
    if not device or device.vendor != "mikrotik":
        raise HTTPException(401, "Credenziali agent non valide.")
    credential.last_used_at = utcnow()
    return device, credential


def _agent_source(base_url: str, device_id: uuid.UUID, raw_secret: str, check_certificate: bool):
    heartbeat = f"{base_url}/api/v1/agents/mikrotik/heartbeat"
    cert_arg = " check-certificate=yes" if check_certificate else ""
    return f''':local nsmDeviceId "{device_id}"
:local nsmSecret "{raw_secret}"
:local nsmUrl "{heartbeat}"
:local nsmIdentity [/system identity get name]
:local nsmModel [/system resource get board-name]
:local nsmVersion [/system resource get version]
:local nsmArch [/system resource get architecture-name]
:local nsmUptime [/system resource get uptime]
:local nsmCpu [/system resource get cpu]
:local nsmCpuCount [/system resource get cpu-count]
:local nsmCpuLoad [/system resource get cpu-load]
:local nsmTotalMemory [/system resource get total-memory]
:local nsmFreeMemory [/system resource get free-memory]
:local nsmSerial ""
:local nsmSoftwareId ""
:local nsmRouterboot ""
:local nsmMac ""
:do {{ :set nsmSerial [/system routerboard get serial-number] }} on-error={{}}
:do {{ :set nsmSoftwareId [/system license get software-id] }} on-error={{}}
:do {{ :set nsmRouterboot [/system routerboard get current-firmware] }} on-error={{}}
:do {{ :local nsmEth [/interface ethernet find]; :if ([:len $nsmEth] > 0) do={{ :set nsmMac [/interface ethernet get ($nsmEth->0) mac-address] }} }} on-error={{}}
:local nsmInventory {{"identity"=$nsmIdentity;"model"=$nsmModel;"routeros_version"=$nsmVersion;"architecture"=$nsmArch;"serial_number"=$nsmSerial;"software_id"=$nsmSoftwareId;"routerboot_version"=$nsmRouterboot;"primary_mac"=$nsmMac;"uptime"=$nsmUptime;"cpu"=$nsmCpu;"cpu_count"=$nsmCpuCount;"total_memory"=$nsmTotalMemory;"free_memory"=$nsmFreeMemory;"agent_version"="{AGENT_VERSION}"}}
:local nsmIfaces ""
:do {{ :foreach nsmIf in=[/interface find where running=yes && dynamic=no && type!="ether" && type!="vlan" && type!="bridge"] do={{ :if ([:len $nsmIfaces] < 6000) do={{ :set nsmIfaces ($nsmIfaces . [/interface get $nsmIf name] . "|" . [/interface get $nsmIf type] . "|" . [/interface get $nsmIf rx-byte] . "|" . [/interface get $nsmIf tx-byte] . ";") }} }} }} on-error={{}}
:do {{ :foreach nsmIf in=[/interface find where running=yes && dynamic=no && (type="ether" || type="vlan" || type="bridge")] do={{ :if ([:len $nsmIfaces] < 6000) do={{ :set nsmIfaces ($nsmIfaces . [/interface get $nsmIf name] . "|" . [/interface get $nsmIf type] . "|" . [/interface get $nsmIf rx-byte] . "|" . [/interface get $nsmIf tx-byte] . ";") }} }} }} on-error={{}}
:local nsmAddrs ""
:do {{ :foreach nsmA in=[/ip address find where disabled=no] do={{ :if ([:len $nsmAddrs] < 2000) do={{ :set nsmAddrs ($nsmAddrs . [/ip address get $nsmA address] . "|" . [/ip address get $nsmA interface] . ";") }} }} }} on-error={{}}
:local nsmMetrics {{"cpu_load"=[:tostr $nsmCpuLoad];"free_memory"=[:tostr $nsmFreeMemory];"uptime"=[:tostr $nsmUptime];"ifaces"=$nsmIfaces;"addresses"=$nsmAddrs}}
:local nsmPayload {{"inventory"=$nsmInventory;"metrics"=$nsmMetrics;"agent_version"="{AGENT_VERSION}"}}
:local nsmJson [:serialize value=$nsmPayload to=json options=json.no-string-conversion]
:do {{
  /tool fetch url=$nsmUrl http-method=post http-header-field=("Content-Type:application/json,X-NSM-Device-ID:" . $nsmDeviceId . ",X-NSM-Device-Secret:" . $nsmSecret) http-data=$nsmJson output=user as-value{cert_arg}
}} on-error={{ :log warning "NSM agent heartbeat failed" }}
'''


def _bootstrap_script(base_url: str, token: str):
    enroll_url = f"{base_url}/api/v1/agents/mikrotik/enroll"
    check_certificate = base_url.lower().startswith("https://")
    cert_arg = " check-certificate=yes" if check_certificate else ""
    return f''':local nsmToken "{token}"
:local nsmEnroll "{enroll_url}"
:local nsmIdentity [/system identity get name]
:local nsmModel [/system resource get board-name]
:local nsmVersion [/system resource get version]
:local nsmArch [/system resource get architecture-name]
:local nsmUptime [/system resource get uptime]
:local nsmCpu [/system resource get cpu]
:local nsmCpuCount [/system resource get cpu-count]
:local nsmTotalMemory [/system resource get total-memory]
:local nsmFreeMemory [/system resource get free-memory]
:local nsmSerial ""
:local nsmSoftwareId ""
:local nsmRouterboot ""
:local nsmMac ""
:do {{ :set nsmSerial [/system routerboard get serial-number] }} on-error={{}}
:do {{ :set nsmSoftwareId [/system license get software-id] }} on-error={{}}
:do {{ :set nsmRouterboot [/system routerboard get current-firmware] }} on-error={{}}
:do {{ :local nsmEth [/interface ethernet find]; :if ([:len $nsmEth] > 0) do={{ :set nsmMac [/interface ethernet get ($nsmEth->0) mac-address] }} }} on-error={{}}
:local nsmInventory {{"identity"=$nsmIdentity;"model"=$nsmModel;"routeros_version"=$nsmVersion;"architecture"=$nsmArch;"serial_number"=$nsmSerial;"software_id"=$nsmSoftwareId;"routerboot_version"=$nsmRouterboot;"primary_mac"=$nsmMac;"uptime"=$nsmUptime;"cpu"=$nsmCpu;"cpu_count"=$nsmCpuCount;"total_memory"=$nsmTotalMemory;"free_memory"=$nsmFreeMemory;"agent_version"="{AGENT_VERSION}"}}
:local nsmPayload {{"token"=$nsmToken;"inventory"=$nsmInventory}}
:local nsmJson [:serialize value=$nsmPayload to=json options=json.no-string-conversion]
:local nsmResult [/tool fetch url=$nsmEnroll http-method=post http-header-field="Content-Type:application/json" http-data=$nsmJson output=user as-value{cert_arg}]
:if (($nsmResult->"status") != "finished") do={{ :error "NSM enrollment HTTP request failed" }}
:local nsmResponse [:deserialize from=json value=($nsmResult->"data") options=json.no-string-conversion]
:if (($nsmResponse->"status") != "ok") do={{ :error "NSM enrollment rejected" }}
:local nsmAgentSource ($nsmResponse->"agent_source")
:do {{ /system scheduler remove [find name="nsm-agent-heartbeat"] }} on-error={{}}
:do {{ /system script remove [find name="nsm-agent-heartbeat"] }} on-error={{}}
/system script add name="nsm-agent-heartbeat" policy=read,test source=$nsmAgentSource comment="NSM managed agent {AGENT_VERSION}"
/system scheduler add name="nsm-agent-heartbeat" interval=2m on-event="/system script run nsm-agent-heartbeat" policy=read,test comment="NSM managed agent"
/system script run nsm-agent-heartbeat
:log info "NSM enrollment completed and heartbeat agent installed"
:do {{ /file remove "nsm-bootstrap.rsc" }} on-error={{}}
'''


@router.get("/api/v1/enrollment/mikrotik/bootstrap", response_class=PlainTextResponse)
def mikrotik_bootstrap(request: Request, token: str):
    with SessionLocal() as db:
        enrollment = core.get_valid_enrollment(db, token)
        if not enrollment:
            raise HTTPException(401, "Enrollment token non valido o scaduto.")
        device = db.get(Device, enrollment.device_id)
        if not device or device.vendor != "mikrotik":
            raise HTTPException(400, "Enrollment non valido per questo dispositivo.")
    return PlainTextResponse(
        _bootstrap_script(str(request.base_url).rstrip("/"), token),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/api/v1/agents/mikrotik/enroll")
async def mikrotik_enroll(request: Request):
    payload = await _json_body(request)
    raw_token = _string(payload.get("token"), 200)
    inventory = payload.get("inventory") if isinstance(payload.get("inventory"), dict) else {}
    if not raw_token:
        raise HTTPException(400, "Token mancante.")

    with SessionLocal() as db:
        enrollment = core.get_valid_enrollment(db, raw_token)
        if not enrollment:
            raise HTTPException(401, "Enrollment token non valido o scaduto.")
        device = db.get(Device, enrollment.device_id)
        if not device or device.vendor != "mikrotik":
            raise HTTPException(400, "Device non valido.")

        before, after, changes = _apply_inventory(
            db, device, inventory, request, "mikrotik_agent"
        )
        raw_secret = secrets.token_urlsafe(32)
        credential = db.scalar(
            select(DeviceAgentCredential).where(
                DeviceAgentCredential.device_id == device.id,
                DeviceAgentCredential.agent_type == "mikrotik_agent",
            )
        )
        if credential:
            credential.secret_hash = _secret_digest(raw_secret)
            credential.is_active = True
            credential.rotated_at = utcnow()
        else:
            credential = DeviceAgentCredential(
                device_id=device.id,
                agent_type="mikrotik_agent",
                secret_hash=_secret_digest(raw_secret),
            )
            db.add(credential)

        enrollment.status = "used"
        enrollment.used_at = utcnow()
        core.add_event(
            db,
            "DEVICE_ENROLLED",
            customer_id=device.customer_id,
            device_id=device.id,
            details={
                "source": "mikrotik_agent",
                "agent_version": AGENT_VERSION,
                "inventory_changes": changes,
            },
            source="mikrotik_agent",
        )
        if before != after:
            core.add_event(
                db,
                "INVENTORY_DISCOVERED",
                customer_id=device.customer_id,
                device_id=device.id,
                details={"before": before, "after": after},
                source="mikrotik_agent",
            )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Inventario in conflitto con un apparato esistente.")

        base_url = str(request.base_url).rstrip("/")
        return {
            "status": "ok",
            "device_id": str(device.id),
            "agent_version": AGENT_VERSION,
            "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
            "agent_source": _agent_source(
                base_url,
                device.id,
                raw_secret,
                base_url.lower().startswith("https://"),
            ),
        }


@router.post("/api/v1/agents/mikrotik/heartbeat")
async def mikrotik_heartbeat(request: Request):
    payload = await _json_body(request)
    inventory = payload.get("inventory") if isinstance(payload.get("inventory"), dict) else {}
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    inventory = {**inventory, "agent_version": payload.get("agent_version") or inventory.get("agent_version")}

    with SessionLocal() as db:
        device, credential = _authenticate_agent(db, request)
        _apply_inventory(db, device, inventory, request, "mikrotik_agent")
        data = dict(device.inventory_data or {})
        data["metrics"] = {k: _string(v, 200) for k, v in metrics.items()}
        data["last_heartbeat_at"] = utcnow().isoformat()
        device.inventory_data = data

        now = utcnow()
        jobs = list(
            db.scalars(
                select(DeviceJob)
                .where(
                    DeviceJob.device_id == device.id,
                    DeviceJob.status == "pending",
                    or_(DeviceJob.not_before.is_(None), DeviceJob.not_before <= now),
                    or_(DeviceJob.expires_at.is_(None), DeviceJob.expires_at > now),
                )
                .order_by(DeviceJob.created_at)
                .limit(5)
            )
        )
        response_jobs = []
        for job in jobs:
            job.status = "delivered"
            job.delivered_at = now
            job.attempts += 1
            response_jobs.append(
                {"id": str(job.id), "type": job.job_type, "payload": job.payload or {}}
            )
        db.commit()
        return {
            "status": "ok",
            "device_id": str(device.id),
            "server_time": now.isoformat(),
            "next_poll_seconds": HEARTBEAT_INTERVAL_SECONDS,
            "jobs": response_jobs,
        }


@router.post("/api/v1/agents/mikrotik/jobs/{job_id}/complete")
async def mikrotik_job_complete(request: Request, job_id: uuid.UUID):
    payload = await _json_body(request)
    with SessionLocal() as db:
        device, _ = _authenticate_agent(db, request)
        job = db.get(DeviceJob, job_id)
        if not job or job.device_id != device.id:
            raise HTTPException(404, "Job non trovato.")
        status = _string(payload.get("status"), 30) or "failed"
        if status not in {"success", "failed"}:
            raise HTTPException(400, "Stato job non valido.")
        job.status = status
        job.result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        job.last_error = _string(payload.get("error"), 4000)
        job.completed_at = utcnow()
        core.add_event(
            db,
            "DEVICE_JOB_COMPLETED",
            customer_id=device.customer_id,
            device_id=device.id,
            details={"job_id": str(job.id), "job_type": job.job_type, "status": status},
            severity="warning" if status == "failed" else "info",
            result=status,
            source="mikrotik_agent",
        )
        db.commit()
        return {"status": "ok"}


def install_mikrotik_agent(app):
    _remove_route(app, "/api/v1/enrollment/mikrotik/bootstrap", "GET")
    _remove_route(app, "/api/v1/enrollment/mikrotik/complete", "POST")
    app.include_router(router)
