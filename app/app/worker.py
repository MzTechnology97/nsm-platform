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
from app.uisp_metrics import cleanup as uisp_metrics_cleanup
from app.interface_traffic import cleanup as interface_traffic_cleanup
from app.syslog_receiver import cleanup as syslog_cleanup
from app.syslog_security import evaluate as evaluate_access_alerts
from app.mikrotik_syslog_config import auto_configure as auto_configure_syslog
from app.zabbix_connector import scheduled_sync as zabbix_sync
from app.zabbix_problems import scheduled_refresh as zabbix_problems_refresh
from app.device_exposure import tick as exposure_tick
from app.external_exposure import tick as external_exposure_tick
from app.syslog_integrity import anchor_heads as syslog_anchor
from app.routeros_catalog import run_scheduled as run_routeros_catalog
from app.routerboot_lifecycle import verification_tick as routerboot_verification_tick
from app.mikrotik_device_reboot import verify_reboots
from app.mikrotik_legacy_operations import verify_upgrades as verify_legacy_upgrades
from app.report_schedules import run_report_schedules
from app.uisp_sync import sync_uisp_devices
from app.advisory_sources import sync_security_advisories
from app.compliance import run_scheduled_evaluation as run_compliance_evaluation
from app.lifecycle_catalog import run_scheduled_reconcile as run_lifecycle_reconcile
from app.worker_status import beat, run_task, started
from app.notification_delivery import deliver_pending as deliver_notifications
from app.notification_chat import poll_telegram_updates, register_channels
from app.notification_digest import run_vulnerability_digest

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
log = logging.getLogger("worker")
r = Redis.from_url(settings.redis_url, decode_responses=True)

MAINTENANCE_INTERVAL_SECONDS = 60
TELEMETRY_MAINTENANCE_INTERVAL_SECONDS = 3600
COMPLIANCE_INTERVAL_SECONDS = 1800
last_compliance = 0.0
last_maintenance = 0.0
last_telemetry_maintenance = 0.0

install_mikrotik_backup_finalization_cleanup()
install_backup_scheduler_capability_guard()
register_channels()

log.info("Worker avviato")
started()
while True:
    try:
        beat()
        now_mono = time.monotonic()
        if now_mono - last_maintenance >= MAINTENANCE_INTERVAL_SECONDS:
            # Each task runs in isolation: a failure is logged and recorded, the others still run.
            stats = run_task("backup_maintenance", maintenance_tick)
            expired_pending_jobs = run_task("jobs_expire_pending", expire_pending_jobs, default=0)
            expired_delivered_jobs = run_task("jobs_expire_delivered", expire_delivered_jobs, default=0)
            expired_agent_updates = run_task("agent_self_update", reconcile_agent_update_states, default=0)
            agent_stats = run_task("agent_health", agent_health_tick)
            firmware_stats = run_task("firmware_activation", reconcile_firmware_activations)
            firmware_plan_stats = run_task("firmware_plans", reconcile_firmware_plan_jobs)
            routerboot_stats = run_task("routerboot_verification", routerboot_verification_tick)
            reboot_stats = dict(run_task("device_reboot_verification", verify_reboots))
            reboot_stats.update({f"upgrade_{k}": v for k, v in run_task("legacy_upgrade_verification", verify_legacy_upgrades).items()})
            if any(reboot_stats.values()):
                log.info("Device reboot verification: %s", reboot_stats)
            uisp_stats = run_task("uisp_sync", sync_uisp_devices)
            report_stats = run_task("report_schedules", run_report_schedules)
            advisory_stats = run_task("security_advisories", sync_security_advisories)
            run_task("telegram_updates", poll_telegram_updates)
            run_task("vulnerability_digest", run_vulnerability_digest)
            run_task("syslog_access_alerts", evaluate_access_alerts)
            run_task("syslog_agent_config", auto_configure_syslog)
            run_task("zabbix_sync", zabbix_sync)
            run_task("zabbix_problems", zabbix_problems_refresh)
            run_task("exposure_check", exposure_tick)
            run_task("exposure_external_check", external_exposure_tick)
            run_task("syslog_chain_anchor", syslog_anchor)
            notify_stats = run_task("notification_delivery", deliver_notifications)
            if notify_stats.get("failed"):
                log.warning("Notifiche esterne non consegnate: %s", notify_stats)
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

        if now_mono - last_compliance >= COMPLIANCE_INTERVAL_SECONDS:
            lifecycle_stats = run_task("lifecycle_reconcile", run_lifecycle_reconcile)
            catalog_stats = run_task("routeros_catalog", run_routeros_catalog)  # an unreachable upgrade server must not stop the worker
            if catalog_stats.get("refresh", {}).get("new_releases"):
                log.info("RouterOS catalog: %s", catalog_stats)
            if lifecycle_stats.get("status_changed"):
                log.info("Lifecycle: %s", lifecycle_stats)
            compliance_stats = run_task("compliance_evaluation", run_compliance_evaluation)
            if compliance_stats.get("changed") or compliance_stats.get("removed"):
                log.info("Compliance: %s", compliance_stats)
            last_compliance = now_mono

        if now_mono - last_telemetry_maintenance >= TELEMETRY_MAINTENANCE_INTERVAL_SECONDS:
            deleted = run_task("telemetry_retention", telemetry_cleanup, default=0) + run_task("uisp_metrics_retention", uisp_metrics_cleanup, default=0) + run_task("interface_traffic_retention", interface_traffic_cleanup, default=0) + run_task("syslog_retention", syslog_cleanup, default=0)
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
