import json
import logging
import time

from redis import Redis

from app.agent_service import mark_stale_agents_offline
from app.config import settings
from app.db import SessionLocal

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("worker")
r = Redis.from_url(settings.redis_url, decode_responses=True)

STALE_CHECK_INTERVAL_SECONDS = 60


def run_stale_check():
    try:
        with SessionLocal() as db:
            changed = mark_stale_agents_offline(db)
            if changed:
                log.info("Agent stale: %s apparati marcati offline", changed)
    except Exception:
        log.exception("Errore controllo agent stale")


def main():
    log.info("Worker avviato")
    last_stale_check = 0.0
    while True:
        try:
            now = time.monotonic()
            if now - last_stale_check >= STALE_CHECK_INTERVAL_SECONDS:
                run_stale_check()
                last_stale_check = now

            item = r.blpop("nsp:jobs:default", timeout=5)
            if item:
                job = json.loads(item[1])
                log.info("Job ricevuto type=%s id=%s", job.get("type"), job.get("id"))
        except Exception:
            log.exception("Errore worker")
            time.sleep(5)


if __name__ == "__main__":
    main()
