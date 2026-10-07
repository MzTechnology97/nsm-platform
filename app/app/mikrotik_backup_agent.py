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
# RouterOS writes `/system backup save` and `/export file=` output
# asynchronously.  The uploader polls every 500 ms (max 30 s) until the file
# exists with a non-zero size that is unchanged between two polls, so an empty
# or still-growing file is never archived as a successful backup.
FILE_SETTLE_MAX_TICKS = 60
# Agent failure steps mapped to an operator explanation.
STEP_HINTS = (
    ("backup-save", "RouterOS ha rifiutato /system backup save: se l'agent è stato installato prima della 0.49.4 (profilo ops-v1) mancano le policy «policy» e «sensitive»; reinstalla l'agent."),
    ("export", "RouterOS ha rifiutato /export file=: verifica spazio libero e permessi dell'agent."),
    ("wait-file", "RouterOS non ha prodotto il file entro 30 secondi."),
    ("file-missing", "Il file di backup non è stato creato su RouterOS."),
    ("file-empty", "RouterOS ha creato un file vuoto: nessun dato da archiviare."),
    ("file-growing", "Il file era ancora in scrittura dopo 30 secondi: backup non archiviato."),
    ("upload-start", "NSM ha rifiutato l'apertura dell'upload (dimensione o stato del job)."),
    ("read", "/file read non ha restituito dati o non è disponibile: su RouterOS 7.13–7.16 aggiorna a 7.17 o successivo."),
    ("upload-chunk", "Invio di un blocco fallito o senza avanzamento: controlla raggiungibilità di NSM e dimensione dei blocchi."),
    ("upload-finish", "NSM ha rifiutato la chiusura dell'upload: dimensione o hash non coerenti."),
    ("config", "L'agent non ha ottenuto la configurazione del backup da NSM."),
)


def explain_backup_error(error: str | None, profile: str | None = None) -> str | None:
    """Append a human explanation to the step reported by the agent."""
    if not error or "at step:" not in error:
        return error
    step = error.split("at step:", 1)[1].strip()
    for prefix, hint in STEP_HINTS:
        if step.startswith(prefix):
            if prefix == "backup-save" and profile == "ops-v2":
                hint = "RouterOS ha rifiutato /system backup save nonostante il profilo ops-v2: controlla spazio libero e log di RouterOS."
            return f"{error} — {hint}"
    return error


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
      :local nsmStep "config"
      :local nsmUploaded 0
      :local nsmBaseName ("nsm-" . [:pick $nsmJobId 0 8])
      :do {{
        :local nsmConfigUrl ($nsmBase . "/api/v1/agents/mikrotik/jobs/" . $nsmJobId . "/backup-config")
        :local nsmConfigResult [/tool fetch url=$nsmConfigUrl http-method=post http-header-field=$nsmHeaders http-data="{{}}" output=user as-value{cert}]
        :local nsmConfig [:deserialize from=json value=($nsmConfigResult->"data") options=json.no-string-conversion]
        :local nsmPassword ($nsmConfig->"backup_password")
        :local nsmFormats ($nsmConfig->"formats")
        :local nsmChunkSize [:tonum ($nsmConfig->"chunk_size")]
        :if (($nsmChunkSize < 1024) || ($nsmChunkSize > 32768)) do={{ :set nsmChunkSize 24576 }}
        :foreach nsmFormat in=$nsmFormats do={{
          :local nsmFileName ""
          :if ($nsmFormat = "mikrotik_binary") do={{
            :set nsmStep "backup-save"
            :set nsmFileName ($nsmBaseName . ".backup")
            :do {{ /file remove [find where (name=$nsmFileName || name=("flash/" . $nsmFileName))] }} on-error={{}}
            /system backup save name=$nsmBaseName password=$nsmPassword encryption=aes-sha256
          }}
          :if ($nsmFormat = "mikrotik_export") do={{
            :set nsmStep "export"
            :set nsmFileName ($nsmBaseName . ".rsc")
            :do {{ /file remove [find where (name=$nsmFileName || name=("flash/" . $nsmFileName))] }} on-error={{}}
            /export file=$nsmBaseName
          }}
          :if ($nsmFileName != "") do={{
            :set nsmStep ("wait-file " . $nsmFileName)
            :local nsmFilePath ""
            :local nsmFileSize 0
            :local nsmPrevSize 0
            :local nsmWaitTicks 0
            :while (($nsmWaitTicks < {FILE_SETTLE_MAX_TICKS}) && (($nsmFileSize = 0) || ($nsmFileSize != $nsmPrevSize))) do={{
              :delay 500ms
              :set nsmWaitTicks ($nsmWaitTicks + 1)
              :local nsmIds [/file find where (name=$nsmFileName || name=("flash/" . $nsmFileName))]
              :if ([:len $nsmIds] > 0) do={{
                :set nsmFilePath [/file get ($nsmIds->0) name]
                :set nsmPrevSize $nsmFileSize
                :set nsmFileSize [:tonum [/file get ($nsmIds->0) size]]
              }}
            }}
            :if ([:len $nsmFilePath] = 0) do={{ :set nsmStep ("file-missing " . $nsmFileName); :error "NSM backup file not created" }}
            :if ($nsmFileSize = 0) do={{ :set nsmStep ("file-empty " . $nsmFilePath); :error "NSM backup file is empty" }}
            :if ($nsmFileSize != $nsmPrevSize) do={{ :set nsmStep ("file-growing " . $nsmFilePath); :error "NSM backup file size did not settle" }}
            :set nsmStep ("upload-start " . $nsmFormat)
            :local nsmStartUrl ($nsmBase . "/api/v1/agents/mikrotik/jobs/" . $nsmJobId . "/artifacts/start")
            :local nsmStartBody [:serialize value={{"artifact_type"=$nsmFormat;"size_bytes"=[:tostr $nsmFileSize]}} to=json options=json.no-string-conversion]
            :local nsmStartResult [/tool fetch url=$nsmStartUrl http-method=post http-header-field=$nsmHeaders http-data=$nsmStartBody output=user as-value{cert}]
            :local nsmStart [:deserialize from=json value=($nsmStartResult->"data") options=json.no-string-conversion]
            :local nsmUploadId [:tostr ($nsmStart->"upload_id")]
            :if ([:len $nsmUploadId] < 8) do={{ :error "NSM upload session not opened" }}
            :local nsmOffset 0
            :while ($nsmOffset < $nsmFileSize) do={{
              :set nsmStep ("read " . $nsmFormat . " @" . $nsmOffset . "/" . $nsmFileSize)
              :local nsmRead [/file read file=$nsmFilePath offset=$nsmOffset chunk-size=$nsmChunkSize as-value]
              :local nsmRaw ($nsmRead->"data")
              :if ([:len $nsmRaw] = 0) do={{ :error "NSM backup read returned no data" }}
              :set nsmStep ("upload-chunk " . $nsmFormat . " @" . $nsmOffset . "/" . $nsmFileSize)
              :local nsmB64 [:convert $nsmRaw to=base64]
              :local nsmChunkBody [:serialize value={{"offset"=[:tostr $nsmOffset];"data"=$nsmB64}} to=json options=json.no-string-conversion]
              :local nsmChunkUrl ($nsmBase . "/api/v1/agents/mikrotik/uploads/" . $nsmUploadId . "/chunk")
              :local nsmChunkResult [/tool fetch url=$nsmChunkUrl http-method=post http-header-field=$nsmHeaders http-data=$nsmChunkBody output=user as-value{cert}]
              :local nsmChunkResponse [:deserialize from=json value=($nsmChunkResult->"data") options=json.no-string-conversion]
              :local nsmNext [:tonum ($nsmChunkResponse->"next_offset")]
              :if (([:typeof $nsmNext] != "num") || ($nsmNext <= $nsmOffset) || ($nsmNext > $nsmFileSize)) do={{ :error "NSM backup upload made no progress" }}
              :set nsmUploaded ($nsmUploaded + ($nsmNext - $nsmOffset))
              :set nsmOffset $nsmNext
            }}
            :set nsmStep ("upload-finish " . $nsmFormat)
            :local nsmFinishUrl ($nsmBase . "/api/v1/agents/mikrotik/uploads/" . $nsmUploadId . "/finish")
            /tool fetch url=$nsmFinishUrl http-method=post http-header-field=$nsmHeaders http-data="{{}}" output=user as-value{cert}
            :do {{ /file remove [find where name=$nsmFilePath] }} on-error={{}}
          }}
        }}
        :set nsmStep "done"
      }} on-error={{ :set nsmJobOk false; :set nsmJobError ("RouterOS backup failed at step: " . $nsmStep) }}
      :foreach nsmLeft in={{($nsmBaseName . ".backup");($nsmBaseName . ".rsc");("flash/" . $nsmBaseName . ".backup");("flash/" . $nsmBaseName . ".rsc")}} do={{
        :do {{ /file remove [find where name=$nsmLeft] }} on-error={{}}
      }}
      :local nsmDoneUrl ($nsmBase . "/api/v1/agents/mikrotik/backup-jobs/" . $nsmJobId . "/complete")
      :local nsmDoneStatus "failed"
      :if ($nsmJobOk) do={{ :set nsmDoneStatus "success" }}
      :local nsmDoneBody [:serialize value={{"status"=$nsmDoneStatus;"error"=$nsmJobError;"result"={{"agent_version"="{AGENT_VERSION}";"step"=$nsmStep;"uploaded_bytes"=[:tostr $nsmUploaded]}}}} to=json options=json.no-string-conversion]
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


@router.post("/api/v1/agents/mikrotik/backup-jobs/{job_id}/complete", name="backup_aware_job_complete")
async def backup_aware_job_complete(request: Request, job_id: uuid.UUID):
    payload = await _json_body(request)
    with SessionLocal() as db:
        device, _ = _authenticate_agent(db, request)
        job = db.get(DeviceJob, job_id)
        if not job or job.device_id != device.id or job.job_type != "backup_mikrotik":
            raise HTTPException(404, "Backup job non trovato.")
        status = str(payload.get("status", "failed")).strip().lower()
        if status not in {"success", "failed"}:
            raise HTTPException(400, "Stato job non valido.")
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        error = str(payload.get("error", "")).strip()[:4000] or None
        if status == "failed":
            error = explain_backup_error(error, (device.inventory_data or {}).get("agent_privilege_profile"))
        job.status = status
        job.result = result
        job.last_error = error
        job.completed_at = utcnow()
        finalize_backup_job(db, device, job, status == "success", error)
        db.commit()
        return {"status": "ok"}


def install_mikrotik_backup_agent(app):
    agent_module.AGENT_VERSION = AGENT_VERSION
    agent_module._agent_source = enhanced_agent_source
    agent_module._bootstrap_script = enhanced_bootstrap_script
    app.include_router(router)
