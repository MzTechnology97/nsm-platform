import json
import logging
import time

from redis import Redis

from app.agent_health_automation import agent_health_tick
from app.backup_maintenance import maintenance_tick
from app.backup_scheduler_capability_guard import install_backup_scheduler_capability_guard
from app.config import settings
from app.device_job_maintenance import expire_delivered_jobs, expire_pending_jobs
from app.mikrotik_agent_update import reconcile_agent_update_states
from app.firmware_activation import reconcile_firmware_activations
from app.firmware_plan_recovery import reconcile_firmware_plan_jobs
from app.mikrotik_backup_finalization_cleanup import install_mikrotik_backup_finalization_cleanup
from app.mikrotik_telemetry import telemetry_cleanup
from app.routerboot_lifecycle import verification_tick as routerboot_verification_tick
from app.report_schedules import run_report_schedules
from app.uisp_sync import sync_uisp_devices
from app.advisory_sources import sync_security_advisories

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
log = logging.getLogger("worker")
r = Redis.from_url(settings.redis_url, decode_responses=True)

MAINTENANCE_INTERVAL_SECONDS = 60
TELEMETRY_MAINTENANCE_INTERVAL_SECONDS = 3600
last_maintenance = 0.0
last_telemetry_maintenance = 0.0

install_mikrotik_backup_finalization_cleanup()
install_backup_scheduler_capability_guard()

log.info("Worker avviato")
while True:
    try:
        now_mono = time.monotonic()
        if now_mono - last_maintenance >= MAINTENANCE_INTERVAL_SECONDS:
            stats = maintenance_tick()
            expired_pending_jobs = expire_pending_jobs()
            expired_delivered_jobs = expire_delivered_jobs()
            expired_agent_updates = reconcile_agent_update_states()
            agent_stats = agent_health_tick()
            firmware_stats = reconcile_firmware_activations()
            firmware_plan_stats = reconcile_firmware_plan_jobs()
            routerboot_stats = routerboot_verification_tick()
            uisp_stats = sync_uisp_devices()
            report_stats = run_report_schedules()
            advisory_stats = sync_security_advisories()
            if any(stats.values()):
                log.info("Backup maintenance: %s", stats)
            if expired_pending_jobs or expired_delivered_jobs or expired_agent_updates:
                log.info(
                    "Agent job maintenance: scaduti pending=%s delivered=%s self-update=%s",
                    expired_pending_jobs,
                    expired_delivered_jobs,
                    expired_agent_updates,
                )
            if any(agent_stats.values()):
                log.info("Agent health automation: %s", agent_stats)
            if firmware_stats.get("success") or firmware_stats.get("failed"):
                log.info("Firmware activation verification: %s", firmware_stats)
            if firmware_plan_stats.get("jobs_expired") or firmware_plan_stats.get("plans_failed"):
                log.info("Firmware plan recovery: %s", firmware_plan_stats)
            if any(routerboot_stats.values()):
                log.info("RouterBOOT verification: %s", routerboot_stats)
            if uisp_stats.get("status") in {"success", "failed"}:
                log.info("UISP sync: %s", uisp_stats)
            if any(report_stats.values()):
                log.info("Report schedules: %s", report_stats)
            if advisory_stats.get("status") in {"success", "failed"}:
                log.info("Security advisories: %s", advisory_stats)
            last_maintenance = now_mono

        if now_mono - last_telemetry_maintenance >= TELEMETRY_MAINTENANCE_INTERVAL_SECONDS:
            deleted = telemetry_cleanup()
            if deleted:
                log.info("Telemetry retention: rimossi %s campioni scaduti", deleted)
            last_telemetry_maintenance = now_mono

        item = r.blpop("nsp:jobs:default", timeout=5)
        if item:
            job = json.loads(item[1])
            log.info("Job ricevuto type=%s id=%s", job.get("type"), job.get("id"))
    except Exception:
        log.exception("Errore worker")
        time.sleep(5)
