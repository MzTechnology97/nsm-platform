"""Production readiness: isolated worker tasks, worker heartbeat, /health and the admin *Sistema* page."""
import gzip
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app import worker_status
from app.db import SessionLocal
from app.entrypoint import app
from app.models import User
from app.security import hash_password

PASSWORD = "CI-System-Status-2026"


class FakeRedis:
    def __init__(self):
        self.values, self.hashes = {}, {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))


class DownRedis:
    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise ConnectionError("redis down")
        return fail


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def boom():
    raise RuntimeError("upstream unreachable")


def main():
    fake = FakeRedis()
    worker_status.use_client(fake)
    assert worker_status.run_task("ok_task", lambda: {"done": 1}) == {"done": 1}
    assert worker_status.run_task("bad_task", boom) == {}, "a failing task returns an empty result instead of raising"
    assert worker_status.run_task("bad_count", boom, default=0) == 0
    worker_status.run_task("bad_task", boom)
    worker_status.beat()
    worker_status.started()
    state = worker_status.status()
    tasks = {t["name"]: t for t in state["tasks"]}
    assert state["available"] and state["alive"] and state["age_seconds"] <= 5
    assert not tasks["ok_task"]["failing"] and tasks["ok_task"]["last_ok"]
    assert tasks["bad_task"]["failing"] and tasks["bad_task"]["failures"] == 2 and "upstream unreachable" in tasks["bad_task"]["last_error"]
    worker_status.run_task("bad_task", lambda: {})
    assert not {t["name"]: t for t in worker_status.status()["tasks"]}["bad_task"]["failing"], "a later success clears the failure"
    stale = worker_status.status(now=datetime.now(timezone.utc) + timedelta(seconds=worker_status.HEARTBEAT_STALE_SECONDS + 5))
    assert not stale["alive"]

    worker_status.use_client(DownRedis())
    assert worker_status.run_task("still_runs", lambda: 7) == 7, "Redis being down never stops a task"
    worker_status.beat()
    assert worker_status.status()["available"] is False

    source = Path(__file__).resolve().parents[1].joinpath("app", "worker.py").read_text(encoding="utf-8")
    assert source.count("run_task(") >= 19 and "beat()" in source, "every periodic worker task runs through run_task"

    # /health keeps its contract and adds the worker state.
    worker_status.use_client(fake)
    worker_status.run_task("bad_task", boom)
    client = TestClient(app)
    health = client.get("/health")
    body = health.json()
    assert "database" in body and "version" in body and body["worker"]["alive"] is True
    assert body["worker"]["failing_tasks"] == ["bad_count", "bad_task"]
    assert client.get("/api/v1/health").json()["worker"]["alive"] is True

    with tempfile.TemporaryDirectory() as folder:
        os.environ["PLATFORM_DB_BACKUP_DIR"] = folder
        dump = Path(folder, "network_platform_20261007T020000Z.sql.gz")
        with gzip.open(dump, "wb") as handle:
            handle.write(b"-- synthetic dump\n")
        now = datetime.now(timezone.utc)
        Path(folder, "restore-drills.jsonl").write_text(
            json.dumps({"at": (now - timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%SZ"), "file": dump.name, "result": "success", "seconds": 4, "tables": "60", "revision": "0023", "devices": "12"}) + "\n"
            + json.dumps({"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "file": dump.name, "result": "failed", "seconds": 1, "tables": "", "revision": "", "devices": ""}) + "\nnot json\n",
            encoding="utf-8")

        suffix = uuid.uuid4().hex[:6]
        with SessionLocal() as db:
            db.add_all([User(username=f"ci-sys-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True),
                        User(username=f"ci-sys-t-{suffix}", password_hash=hash_password(PASSWORD), role="technician", is_active=True)])
            db.commit()
        admin = TestClient(app)
        assert admin.post("/login", data={"username": f"ci-sys-{suffix}", "password": PASSWORD, "csrf": csrf_from(admin.get("/login").text)}, follow_redirects=False).status_code == 303
        page = admin.get("/admin/system").text
        assert 'data-system="worker"' in page and "Attivo" in page and 'data-task="bad_task" data-failing' in page and "upstream unreachable" in page
        assert dump.name in page and "Recente" in page, "a fresh platform dump is reported"
        assert "Prova scaduta" in page and "fallita" in page and "riuscita" in page, "the last successful drill is 200 days old"
        assert 'href="/admin/system"' in admin.get("/admin/users").text

        os.utime(dump, (now.timestamp() - 10 * 86400, now.timestamp() - 10 * 86400))
        assert "Da eseguire" in admin.get("/admin/system").text, "a dump older than 7 days is stale"

        worker_status.use_client(DownRedis())
        assert "Redis non raggiungibile" in admin.get("/admin/system").text

        tech = TestClient(app)
        assert tech.post("/login", data={"username": f"ci-sys-t-{suffix}", "password": PASSWORD, "csrf": csrf_from(tech.get("/login").text)}, follow_redirects=False).status_code == 303
        assert tech.get("/admin/system", follow_redirects=False).status_code in (401, 403)
        del os.environ["PLATFORM_DB_BACKUP_DIR"]
    print("System status smoke passed")


if __name__ == "__main__":
    main()
