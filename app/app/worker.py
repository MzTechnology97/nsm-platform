import json
import logging
import time

from redis import Redis

from app.agent_health_automation import agent_health_tick
from app.backup_maintenance import maintenance_tick
from app.backup_scheduler_capability_guard import install_backup_scheduler_capability_guard
from app.config import settings
from app.firmware_activation import reconcile_firmware_activations
from app.mikrotik_telemetry import telemetry_cleanup
from app.routerboot_lifecycle import verification_tick as routerboot_verification_tick

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
log = logging.getLogger("worker")
r = Redis.from_url(settings.redis_url, decode_responses=True)

MAINTENANCE_INTERVAL_SECONDS = 60
TELEMETRY_MAINTENANCE_INTERVAL_SECONDS = 3600
last_maintenance = 0.0
last_telemetry_maintenance = 0.0

install_backup_scheduler_capability_guard()

log.info("Worker avviato")
while True:
    try:
        now_mono = time.monotonic()
        if now_mono - last_maintenance >= MAINTENANCE_INTERVAL_SECONDS:
            stats = maintenance_tick()
            agent_stats = agent_health_tick()
            firmware_stats = reconcile_firmware_activations()
            routerboot_stats = routerboot_verification_tick()
            if any(stats.values()):
                log.info("Backup maintenance: %s", stats)
            if any(agent_stats.values()):
                log.info("Agent health automation: %s", agent_stats)
            if firmware_stats.get("success") or firmware_stats.get("failed"):
                log.info("Firmware activation verification: %s", firmware_stats)
            if any(routerboot_stats.values()):
                log.info("RouterBOOT verification: %s", routerboot_stats)
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
