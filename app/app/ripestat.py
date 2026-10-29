"""RIPEstat (RIPESTAT-01): the operator's prefixes, BGP and RPKI from the free RIPEstat Data API, read-only.

No account and no key: every request carries ``sourceapp=nsm``. The module is off by default (MOD-01); the operator
gives its ASNs, and the declared public prefixes are the ones of the threat lists (``threat_lists.operator_prefixes``).
Every ``refresh_hours`` NSM reads:

- ``announced-prefixes`` of each ASN: what the world sees announced by the operator;
- ``routing-status`` of each declared prefix: origin ASNs and RIS visibility. Announced by an ASN that is not the
  operator's → possible hijack (critical); not announced at all → warning; seen by less than half of the RIS peers →
  warning;
- ``rpki-validation`` of each announced prefix with its ASN: *invalid* → critical (routes may be dropped), *unknown* (no
  ROA) → warning, with what to create in the RIPE portal.

Announced prefixes not covered by a declared prefix are proposed as prefixes to declare; nothing is added on its own.
Problems are Action Center issues (``ripestat``), closed when the next read no longer shows them. NSM never changes
anything at RIPE.
"""
from __future__ import annotations

import ipaddress
from datetime import timedelta

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app import main as core
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app.models import ActionIssue, Notification, utcnow
from app.ui_api import require_csrf, ui_user

router = APIRouter(prefix="/api/ui/v1", tags=["ui"])
PROVIDER = "ripestat"
API = "https://stat.ripe.net/data"
ISSUE_CATEGORY = "ripestat"
TIMEOUT = 30.0
MAX_ASNS = 16
MAX_CALLS = 300
MIN_VISIBILITY = 0.5
DEFAULT_REFRESH_HOURS = 6
TRANSPORT = None  # tests replace it
PROBLEMS = {  # kind: (severity, title, what to do)
    "foreign_origin": ("critical", "annunciato da un altro AS", "Verifica subito: un altro AS annuncia il tuo prefisso (possibile hijack). Contatta il tuo upstream e crea/aggiorna i ROA."),
    "not_announced": ("warning", "non annunciato", "Il prefisso dichiarato non è visibile in BGP: se dovrebbe esserlo, controlla le sessioni BGP e i filtri dell'upstream."),
    "low_visibility": ("warning", "poco visibile in BGP", "Meno della metà dei peer RIS vede il prefisso: controlla filtri, ROA e propagazione presso gli upstream."),
    "rpki_invalid": ("critical", "RPKI non valido", "L'annuncio non corrisponde ai ROA: molti operatori lo scartano. Correggi il ROA (AS di origine, max length) nel portale RIPE."),
    "rpki_unknown": ("warning", "senza ROA", "Nessun ROA copre l'annuncio: crealo nel portale RIPE (RPKI Dashboard) con il tuo AS e la lunghezza del prefisso."),
}


class InvalidSettings(ValueError):
    pass


class RipestatError(RuntimeError):
    pass


# --- Settings ------------------------------------------------------------------------------------

def _row(db):
    return db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == PROVIDER))


def load(db) -> dict:
    row = _row(db)
    saved = dict((row.settings or {}) if row else {})
    return {"enabled": bool(row and row.is_enabled), "asns": saved.get("asns") or [], "refresh_hours": saved.get("refresh_hours", DEFAULT_REFRESH_HOURS),
            "prefixes": saved.get("prefixes") or [], "announced": saved.get("announced") or [], "suggested": saved.get("suggested") or [],
            "refreshed_at": saved.get("refreshed_at"), "errors": saved.get("errors") or []}


def _parse_asns(raw) -> list[int]:
    values = raw if isinstance(raw, list) else str(raw or "").replace(",", " ").split()
    out = []
    for value in (str(v).strip().upper().removeprefix("AS") for v in values):
        if not value:
            continue
        if not value.isdigit() or not 0 < int(value) < 4294967296:
            raise InvalidSettings(f"Numero AS non valido: {value[:20]}")
        out.append(int(value))
    out = list(dict.fromkeys(out))
    if len(out) > MAX_ASNS:
        raise InvalidSettings(f"Al massimo {MAX_ASNS} AS.")
    return out


def save(db, user, body: dict) -> None:
    row = _row(db)
    if row is None:
        row = ConnectorIntegration(provider=PROVIDER, name="RIPEstat", base_url=API, verify_tls=True, secret_encrypted="", settings={})
        db.add(row)
    asns = _parse_asns(body.get("asns"))
    try:
        hours = int(body.get("refresh_hours", DEFAULT_REFRESH_HOURS))
    except (TypeError, ValueError):
        raise InvalidSettings("Intervallo non valido.") from None
    if not 1 <= hours <= 168:
        raise InvalidSettings("L'intervallo deve essere tra 1 e 168 ore.")
    enabled = body.get("enabled") is True
    if enabled and not asns:
        raise InvalidSettings("Indica almeno un numero AS prima di attivare il modulo.")
    row.settings = {**dict(row.settings or {}), "asns": asns, "refresh_hours": hours}
    row.is_enabled = enabled
    core.add_event(db, "RIPESTAT_CONFIGURED", actor=user, source="portal", details={"enabled": enabled, "asns": asns, "refresh_hours": hours})


# --- Reads ---------------------------------------------------------------------------------------

def call(client: httpx.Client, name: str, params: dict) -> dict:
    try:
        response = client.get(f"/{name}/data.json", params={**params, "sourceapp": "nsm"})
    except httpx.HTTPError as exc:
        raise RipestatError(f"RIPEstat non raggiungibile ({name}).") from exc
    if response.status_code != 200:
        raise RipestatError(f"RIPEstat {name}: HTTP {response.status_code}.")
    body = response.json()
    if body.get("status") != "ok":
        messages = "; ".join(str(m[1]) for m in body.get("messages") or [] if len(m) > 1)[:200]
        raise RipestatError(f"RIPEstat {name}: {messages or body.get('status')}")
    return body.get("data") or {}


def _covered(prefix: str, others) -> bool:
    network = ipaddress.ip_network(prefix)
    return any(network.version == o.version and network.subnet_of(o) for o in others)


def read(asns: list[int], declared: list[str]) -> dict:
    """Everything RIPEstat says about the operator's ASNs and declared prefixes (no database access)."""
    errors, calls = [], 0
    with httpx.Client(base_url=API, timeout=TIMEOUT, transport=TRANSPORT, headers={"User-Agent": "NSM"}) as client:
        def get(name, params):
            nonlocal calls
            if calls >= MAX_CALLS:
                raise RipestatError("Troppe richieste in un controllo: riduci AS o prefissi.")
            calls += 1
            return call(client, name, params)

        announced = []
        for asn in asns:
            try:
                rows = get("announced-prefixes", {"resource": f"AS{asn}"}).get("prefixes") or []
                announced += [{"prefix": str(r["prefix"]), "asn": asn} for r in rows if r.get("prefix")]
            except RipestatError as exc:
                errors.append(str(exc))
        prefixes = []
        for prefix in declared:
            entry = {"prefix": prefix, "origins": [], "seeing": None, "total": None, "problems": []}
            try:
                data = get("routing-status", {"resource": prefix})
            except RipestatError as exc:
                errors.append(str(exc))
                prefixes.append({**entry, "error": str(exc)})
                continue
            entry["origins"] = sorted({int(o["origin"]) for o in data.get("origins") or [] if str(o.get("origin", "")).isdigit()})
            visibility = (data.get("visibility") or {}).get("v6" if ipaddress.ip_network(prefix).version == 6 else "v4") or {}
            entry["seeing"], entry["total"] = visibility.get("ris_peers_seeing"), visibility.get("total_ris_peers")
            if any(origin not in asns for origin in entry["origins"]):
                entry["problems"].append("foreign_origin")
            if not entry["origins"]:
                entry["problems"].append("not_announced")
            elif entry["total"] and (entry["seeing"] or 0) / entry["total"] < MIN_VISIBILITY:
                entry["problems"].append("low_visibility")
            prefixes.append(entry)
        for item in announced:
            try:
                item["rpki"] = str(get("rpki-validation", {"resource": item["asn"], "prefix": item["prefix"]}).get("status") or "unknown").lower()
            except RipestatError as exc:
                errors.append(str(exc))
                item["rpki"] = None
            item["problems"] = {"invalid": ["rpki_invalid"], "unknown": ["rpki_unknown"]}.get(item["rpki"] or "", [])
    networks = [ipaddress.ip_network(p) for p in declared]
    suggested = [a["prefix"] for a in announced if not _covered(a["prefix"], networks)]
    return {"prefixes": prefixes, "announced": announced, "suggested": list(dict.fromkeys(suggested)), "errors": errors[:20], "calls": calls}


# --- Issues --------------------------------------------------------------------------------------

def _title(prefix: str, kind: str) -> str:
    return f"RIPEstat: {prefix} {PROBLEMS[kind][1]}"


def sync_issues(db, result: dict) -> dict:
    """Open an issue per problem, close the ones no longer seen (caller commits)."""
    found = [(p["prefix"], kind, p) for p in result["prefixes"] for kind in p["problems"]]
    found += [(a["prefix"], kind, a) for a in result["announced"] for kind in a.get("problems") or []]
    keep, opened = set(), 0
    for prefix, kind, item in found:
        severity, _label, advice = PROBLEMS[kind]
        title = _title(prefix, kind)
        keep.add(title)
        details = {"prefix": prefix, "kind": kind, "origins": item.get("origins"), "asn": item.get("asn"), "seeing": item.get("seeing"),
                   "total": item.get("total"), "rpki": item.get("rpki"), "advice": advice}
        issue = db.scalar(select(ActionIssue).where(ActionIssue.category == ISSUE_CATEGORY, ActionIssue.title == title,
                                                    ActionIssue.status.in_(["open", "acknowledged"])))
        if issue:
            issue.details, issue.updated_at = details, utcnow()
            continue
        db.add(ActionIssue(category=ISSUE_CATEGORY, severity=severity, status="open", title=title, details=details))
        db.add(Notification(severity=severity, category="security", title=title, message=advice, source_url="/admin/integrations/ripestat", is_active=True))
        core.add_event(db, "RIPESTAT_ALERT", severity="warning", source="worker", details={"prefix": prefix, "kind": kind})
        opened += 1
    closed = 0
    for issue in db.scalars(select(ActionIssue).where(ActionIssue.category == ISSUE_CATEGORY, ActionIssue.status.in_(["open", "acknowledged"]))):
        if issue.title not in keep:
            issue.status, issue.resolved_at = "resolved", utcnow()
            issue.details = {**(issue.details or {}), "resolved_reason": "non più presente all'ultima lettura RIPEstat"}
            closed += 1
    return {"problems": len(keep), "opened": opened, "closed": closed}


def refresh(db, row) -> dict:
    from app import threat_lists

    settings = dict(row.settings or {})
    result = read(settings.get("asns") or [], threat_lists.load(db)["operator_prefixes"])
    if result["errors"] and all(p.get("error") for p in result["prefixes"]) and not result["announced"]:
        # Nothing read at all (RIPEstat down): keep the previous picture and its issues.
        row.settings = {**settings, "errors": result["errors"], "failed_at": utcnow().isoformat()}
        return {"status": "failed", "errors": result["errors"]}
    row.settings = {**settings, **{k: result[k] for k in ("prefixes", "announced", "suggested", "errors")}, "refreshed_at": utcnow().isoformat()}
    return {"status": "refreshed", "calls": result["calls"], **sync_issues(db, result)}


def tick(now=None) -> dict:
    now = now or utcnow()
    with SessionLocal() as db:
        row = _row(db)
        if row is None or not row.is_enabled:
            return {"status": "disabled"}
        settings = row.settings or {}
        last = settings.get("refreshed_at") or settings.get("failed_at")
        if last and last > (now - timedelta(hours=settings.get("refresh_hours", DEFAULT_REFRESH_HOURS))).isoformat():
            return {"status": "not_due"}
        stats = refresh(db, row)
        db.commit()
        return stats


# --- Pages ---------------------------------------------------------------------------------------

def _payload(db) -> dict:
    from app import threat_lists

    return jsonable_encoder({**load(db), "declared": threat_lists.load(db)["operator_prefixes"],
                             "problem_labels": {kind: meta[1] for kind, meta in PROBLEMS.items()},
                             "advice": {kind: meta[2] for kind, meta in PROBLEMS.items()}})


@router.get("/admin/integrations/ripestat")
def admin_ripestat(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        return _payload(db)


@router.post("/admin/integrations/ripestat", dependencies=[Depends(require_csrf)])
def admin_ripestat_save(request: Request, body: dict = Body(default={})):
    with SessionLocal() as db:
        user = ui_user(request, db, "users.manage")
        try:
            save(db, user, body or {})
        except InvalidSettings as error:
            raise HTTPException(422, {"code": "invalid", "message": str(error)}) from None
        db.commit()
        return _payload(db)


@router.post("/admin/integrations/ripestat/refresh", dependencies=[Depends(require_csrf)])
def admin_ripestat_refresh(request: Request):
    with SessionLocal() as db:
        ui_user(request, db, "users.manage")
        row = _row(db)
        if row is None or not row.is_enabled:
            raise HTTPException(409, {"code": "disabled", "message": "Attiva prima il modulo RIPEstat."})
        stats = refresh(db, row)
        db.commit()
        return {"stats": stats, **_payload(db)}


def install_ripestat(app) -> None:
    app.include_router(router)
