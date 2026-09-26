import uuid

from fastapi import APIRouter, HTTPException, Request

from app import main as core
from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.mikrotik_agent import _authenticate_agent, _json_body
from app.mikrotik_backup import finalize_backup_job
from app.models import utcnow
import app.mikrotik_agent as agent_module

router = APIRouter()
_ORIGINAL_BOOTSTRAP = agent_module._bootstrap_script
AGENT_VERSION = "0.8.0"


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


def _cert_arg(enabled: bool):
    return " check-certificate=yes" if enabled else ""


def enhanced_agent_source(base_url: str, device_id: uuid.UUID, raw_secret: str, check_certificate: bool):
    heartbeat = f"{base_url}/api/v1/agents/mikrotik/heartbeat"
    cert = _cert_arg(check_certificate)
    return f''':local nsmDeviceId "{device_id}"
:local nsmSecret "{raw_secret}"
:local nsmBase "{base_url}"
:local nsmHeartbeat "{heartbeat}"
:local nsmHeaders ("Content-Type:application/json,X-NSM-Device-ID:" . $nsmDeviceId . ",X-NSM-Device-Secret:" . $nsmSecret)
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
:local nsmMetrics {{"cpu_load"=[:tostr $nsmCpuLoad];"free_memory"=[:tostr $nsmFreeMemory];"uptime"=[:tostr $nsmUptime]}}
:local nsmPayload {{"inventory"=$nsmInventory;"metrics"=$nsmMetrics;"agent_version"="{AGENT_VERSION}"}}
:local nsmJson [:serialize value=$nsmPayload to=json options=json.no-string-conversion]
:local nsmHeartbeatResult ""
:do {{ :set nsmHeartbeatResult [/tool fetch url=$nsmHeartbeat http-method=post http-header-field=$nsmHeaders http-data=$nsmJson output=user as-value{cert}] }} on-error={{ :log warning "NSM agent heartbeat failed"; :return }}
:if (($nsmHeartbeatResult->"status") != "finished") do={{ :log warning "NSM agent heartbeat HTTP failure"; :return }}
:local nsmResponse [:deserialize from=json value=($nsmHeartbeatResult->"data") options=json.no-string-conversion]
:local nsmJobs ($nsmResponse->"jobs")
:if ([:typeof $nsmJobs] = "array") do={{
  :foreach nsmJob in=$nsmJobs do={{
    :local nsmJobId ($nsmJob->"id")
    :local nsmJobType ($nsmJob->"type")
    :if ($nsmJobType = "inventory_refresh") do={{
      :local nsmDoneUrl ($nsmBase . "/api/v1/agents/mikrotik/jobs/" . $nsmJobId . "/complete")
      :local nsmDoneBody [:serialize value={{"status"="success";"result"={{"refreshed"=true}}}} to=json options=json.no-string-conversion]
      :do {{ /tool fetch url=$nsmDoneUrl http-method=post http-header-field=$nsmHeaders http-data=$nsmDoneBody output=user as-value{cert} }} on-error={{}}
    }}
    :if ($nsmJobType = "backup_mikrotik") do={{
      :local nsmJobOk true
      :local nsmJobError ""
      :do {{
        :local nsmConfigUrl ($nsmBase . "/api/v1/agents/mikrotik/jobs/" . $nsmJobId . "/backup-config")
        :local nsmConfigResult [/tool fetch url=$nsmConfigUrl http-method=post http-header-field=$nsmHeaders http-data="{{}}" output=user as-value{cert}]
        :local nsmConfig [:deserialize from=json value=($nsmConfigResult->"data") options=json.no-string-conversion]
        :local nsmPassword ($nsmConfig->"backup_password")
        :local nsmFormats ($nsmConfig->"formats")
        :local nsmChunkSize [:tonum ($nsmConfig->"chunk_size")]
        :if (($nsmChunkSize < 1024) || ($nsmChunkSize > 32768)) do={{ :set nsmChunkSize 24576 }}
        :foreach nsmFormat in=$nsmFormats do={{
          :local nsmBaseName ("nsm-" . [:pick $nsmJobId 0 8])
          :local nsmFileName ""
          :if ($nsmFormat = "mikrotik_binary") do={{
            /system backup save name=$nsmBaseName password=$nsmPassword encryption=aes-sha256
            :set nsmFileName ($nsmBaseName . ".backup")
          }}
          :if ($nsmFormat = "mikrotik_export") do={{
            /export file=$nsmBaseName
            :set nsmFileName ($nsmBaseName . ".rsc")
          }}
          :if ($nsmFileName != "") do={{
            :delay 500ms
            :local nsmFileId [/file find where name=$nsmFileName]
            :if ([:len $nsmFileId] = 0) do={{ :error "NSM backup file not created" }}
            :local nsmFileSize [:tonum [/file get $nsmFileId size]]
            :local nsmStartUrl ($nsmBase . "/api/v1/agents/mikrotik/jobs/" . $nsmJobId . "/artifacts/start")
            :local nsmStartBody [:serialize value={{"artifact_type"=$nsmFormat;"size_bytes"=[:tostr $nsmFileSize]}} to=json options=json.no-string-conversion]
            :local nsmStartResult [/tool fetch url=$nsmStartUrl http-method=post http-header-field=$nsmHeaders http-data=$nsmStartBody output=user as-value{cert}]
            :local nsmStart [:deserialize from=json value=($nsmStartResult->"data") options=json.no-string-conversion]
            :local nsmUploadId ($nsmStart->"upload_id")
            :local nsmOffset 0
            :while ($nsmOffset < $nsmFileSize) do={{
              :local nsmRead [/file read file=$nsmFileName offset=$nsmOffset chunk-size=$nsmChunkSize]
              :local nsmRaw ($nsmRead->"data")
              :local nsmB64 [:convert $nsmRaw to=base64]
              :local nsmChunkBody [:serialize value={{"offset"=[:tostr $nsmOffset];"data"=$nsmB64}} to=json options=json.no-string-conversion]
              :local nsmChunkUrl ($nsmBase . "/api/v1/agents/mikrotik/uploads/" . $nsmUploadId . "/chunk")
              :local nsmChunkResult [/tool fetch url=$nsmChunkUrl http-method=post http-header-field=$nsmHeaders http-data=$nsmChunkBody output=user as-value{cert}]
              :local nsmChunkResponse [:deserialize from=json value=($nsmChunkResult->"data") options=json.no-string-conversion]
              :set nsmOffset [:tonum ($nsmChunkResponse->"next_offset")]
            }}
            :local nsmFinishUrl ($nsmBase . "/api/v1/agents/mikrotik/uploads/" . $nsmUploadId . "/finish")
            /tool fetch url=$nsmFinishUrl http-method=post http-header-field=$nsmHeaders http-data="{{}}" output=user as-value{cert}
            :do {{ /file remove $nsmFileId }} on-error={{}}
          }}
        }}
      }} on-error={{ :set nsmJobOk false; :set nsmJobError "RouterOS backup/upload failed" }}
      :local nsmDoneUrl ($nsmBase . "/api/v1/agents/mikrotik/jobs/" . $nsmJobId . "/complete")
      :local nsmDoneStatus "failed"
      :if ($nsmJobOk) do={{ :set nsmDoneStatus "success" }}
      :local nsmDoneBody [:serialize value={{"status"=$nsmDoneStatus;"error"=$nsmJobError;"result"={{"agent_version"="{AGENT_VERSION}"}}}} to=json options=json.no-string-conversion]
      :do {{ /tool fetch url=$nsmDoneUrl http-method=post http-header-field=$nsmHeaders http-data=$nsmDoneBody output=user as-value{cert} }} on-error={{ :log warning "NSM backup completion report failed" }}
    }}
  }}
}}
'''


def enhanced_bootstrap_script(base_url: str, token: str):
    script = _ORIGINAL_BOOTSTRAP(base_url, token)
    script = script.replace(
        'policy=read,test source=$nsmAgentSource',
        'policy=read,write,test,sensitive source=$nsmAgentSource',
    )
    script = script.replace(
        'policy=read,test comment="NSM managed agent"',
        'policy=read,write,test,sensitive comment="NSM managed agent"',
    )
    return script


@router.post("/api/v1/agents/mikrotik/jobs/{job_id}/complete")
async def backup_aware_job_complete(request: Request, job_id: uuid.UUID):
    payload = await _json_body(request)
    with SessionLocal() as db:
        device, _ = _authenticate_agent(db, request)
        job = db.get(DeviceJob, job_id)
        if not job or job.device_id != device.id:
            raise HTTPException(404, "Job non trovato.")
        status = str(payload.get("status", "failed")).strip().lower()
        if status not in {"success", "failed"}:
            raise HTTPException(400, "Stato job non valido.")
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        error = str(payload.get("error", "")).strip()[:4000] or None
        job.status = status
        job.result = result
        job.last_error = error
        job.completed_at = utcnow()
        if job.job_type == "backup_mikrotik":
            finalize_backup_job(db, device, job, status == "success", error)
        else:
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


def install_mikrotik_backup_agent(app):
    agent_module.AGENT_VERSION = AGENT_VERSION
    agent_module._agent_source = enhanced_agent_source
    agent_module._bootstrap_script = enhanced_bootstrap_script
    _remove_route(app, "/api/v1/agents/mikrotik/jobs/{job_id}/complete", "POST")
    app.include_router(router)
