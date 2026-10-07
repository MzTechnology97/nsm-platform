"""Connector health summary for the *Sistema* page (production readiness).

One row per data source with a state (``ok``, ``warning``, ``error``,
``disabled``, ``unconfigured``), the last success and the last error, read
from what each connector already stores.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.agent_models import DeviceAgentCredential
from app.integration_models import ConnectorIntegration
from app.models import RouterosRelease, utcnow
from app.uisp_connector import _parse_seen

STALE_AGENT_AFTER = timedelta(minutes=15)
CATALOG_STALE_AFTER = timedelta(hours=24)


def _synced(row: ConnectorIntegration | None, name: str, href: str) -> dict:
    if not row:
        return {"name": name, "state": "unconfigured", "href": href, "last_ok": None, "detail": "non configurato", "error": None}
    if not row.is_enabled:
        return {"name": name, "state": "disabled", "href": href, "last_ok": None, "detail": "disabilitato", "error": None}
    sync = dict((row.settings or {}).get("sync") or {})
    failures = int(sync.get("consecutive_failures") or 0)
    status = sync.get("last_status")
    state = "error" if failures >= 3 else ("warning" if status == "failed" or not status else "ok")
    detail = "mai sincronizzato" if not status else (f"{failures} errori consecutivi" if failures else "sincronizzazione regolare")
    if sync.get("next_attempt_at") and failures:
        detail += f", prossimo tentativo {str(sync['next_attempt_at'])[:16].replace('T', ' ')} UTC"
    return {"name": name, "state": state, "href": href, "last_ok": _parse_seen(sync.get("last_success_at")),
            "detail": detail, "error": sync.get("last_error") if status == "failed" else None}


def connectors(db, now=None) -> list[dict]:
    now = now or utcnow()
    rows = {r.provider: r for r in db.scalars(select(ConnectorIntegration))}
    out = [
        _synced(rows.get("uisp"), "UISP Network", "/admin/integrations/uisp"),
        _synced(rows.get("nvd"), "Advisory NVD", "/admin/integrations/nvd"),
    ]

    acs = rows.get("genieacs")
    if not acs:
        out.append({"name": "GenieACS / TR-069", "state": "unconfigured", "href": "/admin/integrations/genieacs", "last_ok": None, "detail": "non configurato", "error": None})
    elif not acs.is_enabled:
        out.append({"name": "GenieACS / TR-069", "state": "disabled", "href": "/admin/integrations/genieacs", "last_ok": None, "detail": "disabilitato", "error": None})
    else:
        state = {"success": "ok", "failed": "error"}.get(acs.last_test_status or "", "warning")
        out.append({"name": "GenieACS / TR-069", "state": state, "href": "/admin/integrations/genieacs",
                    "last_ok": acs.last_sync_at or (acs.last_tested_at if acs.last_test_status == "success" else None),
                    "detail": "test di connessione riuscito" if state == "ok" else ("test fallito" if state == "error" else "mai testato"),
                    "error": acs.last_error if state == "error" else None})

    fetched = db.scalar(select(func.max(RouterosRelease.fetched_at)))
    catalog_state = "warning" if not fetched else ("warning" if now - fetched > CATALOG_STALE_AFTER else "ok")
    out.append({"name": "Catalogo RouterOS", "state": catalog_state, "href": "/admin/integrations/routeros", "last_ok": fetched,
                "detail": "mai letto" if not fetched else ("non aggiornato da oltre 24 ore" if catalog_state == "warning" else "aggiornato ogni 6 ore"),
                "error": None})

    agents = list(db.scalars(select(DeviceAgentCredential.last_used_at).where(
        DeviceAgentCredential.agent_type == "mikrotik_agent", DeviceAgentCredential.is_active.is_(True))))
    stale = sum(1 for seen in agents if not seen or now - seen > STALE_AGENT_AFTER)
    if not agents:
        agent_row = {"state": "unconfigured", "detail": "nessun agent registrato"}
    else:
        agent_row = {"state": "ok" if not stale else ("error" if stale == len(agents) else "warning"),
                     "detail": f"{len(agents) - stale} di {len(agents)} con heartbeat negli ultimi 15 minuti"}
    out.append({"name": "Agent MikroTik", "href": "/operations/agents", "last_ok": max((s for s in agents if s), default=None), "error": None, **agent_row})
    from app.notification_delivery import delivery_health

    health = delivery_health(db, now)
    if not health["configured"]:
        out.append({"name": "Notifiche e-mail", "state": "unconfigured", "href": "/admin/notifications", "last_ok": None, "detail": "server SMTP non configurato", "error": None})
    else:
        state = "error" if health["failed"] else ("warning" if health["stuck"] else "ok")
        out.append({"name": "Notifiche e-mail", "state": state, "href": "/admin/notifications", "last_ok": None,
                    "detail": f"24 ore: {health['sent']} inviate, {health['pending']} in coda, {health['failed']} fallite", "error": None})
    return out
