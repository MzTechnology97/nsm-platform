"""Security advisory ingestion (SEC-01): NVD CVE API 2.0 adapter.

Responsibilities are split on purpose:

* ``parse_nvd_item`` turns one NVD record into a normalized advisory dict
  (identity, severity, provenance, ``match_rules``). It is pure and knows
  nothing about Devices;
* ``fetch_nvd`` pages through the API for the tracked CPE (RouterOS) with the
  documented limits: ``lastModStartDate``/``lastModEndDate`` together, at most
  120 days apart, ``apiKey`` header optional, pauses between pages;
* ``ingest_advisories`` upserts by CVE id and is idempotent: an unchanged record
  only refreshes ``fetched_at``;
* ``advisory_matching`` decides which Devices are affected.

Connector health and the incremental cursor live in
``ConnectorIntegration(provider="nvd").settings["sync"]``. Per-record parser
failures are kept (bounded) and shown in the admin page instead of aborting
the run; transport failures back off exponentially and open one Action Center
issue after repeated failures.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app import main as core
from app.advisory_matching import reconcile_advisory_matches
from app.vulnerability_remediation import housekeeping
from app.db import SessionLocal
from app.integration_models import ConnectorIntegration
from app import vendor_cpe
from app.models import Device, SecurityAdvisory, utcnow
from app.secret_vault import decrypt_text
from app.uisp_sync import _open_issue, _resolve_issue

PROVIDER = "nvd"
NVD_DEFAULT_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
NVD_DETAIL_URL = "https://nvd.nist.gov/vuln/detail/"
TRACKED_CPES = ("cpe:2.3:o:mikrotik:routeros:*:*:*:*:*:*:*:*",)
PRODUCT_LABELS = {("mikrotik", "routeros"): ("MikroTik", "RouterOS")}

RESULTS_PER_PAGE = 2000
MAX_WINDOW = timedelta(days=119)
CURSOR_OVERLAP = timedelta(hours=2)
PAGE_PAUSE_SECONDS = {True: 1.0, False: 6.5}  # with / without API key
HTTP_TIMEOUT_SECONDS = 30.0
MAX_PAGES = 50

INTERVAL_DEFAULT_MINUTES = 360
INTERVAL_MIN_MINUTES = 60
INTERVAL_MAX_MINUTES = 1440
MATCH_INTERVAL = timedelta(minutes=15)
MAX_BACKOFF = timedelta(hours=24)
MAX_PARSE_ERRORS_KEPT = 20
CONNECTOR_ISSUE_AFTER_FAILURES = 3
CONNECTOR_ISSUE_TITLE = "Fonte advisory NVD non raggiungibile"

SEVERITIES = ("critical", "high", "medium", "low")


class AdvisorySourceError(Exception):
    """The source could not be read (transport, HTTP status, payload shape)."""


class AdvisoryParseError(ValueError):
    """One record could not be normalized; the run continues without it."""


# ---------------------------------------------------------------- parsing --

_CPE_SPLIT = re.compile(r"(?<!\\):")


def parse_cpe(criteria: str) -> dict:
    parts = _CPE_SPLIT.split(str(criteria or ""))
    if len(parts) < 7 or parts[0] != "cpe" or parts[1] != "2.3":
        raise AdvisoryParseError(f"CPE non valida: {criteria!r}")

    def clean(value):
        return re.sub(r"\\(.)", r"\1", value)

    return {
        "part": parts[2],
        "vendor": clean(parts[3]).lower(),
        "product": clean(parts[4]).lower(),
        "version": clean(parts[5]),
        "update": clean(parts[6]),
    }


def _exact_version(cpe: dict) -> str | None:
    version = cpe["version"]
    if version in ("*", "-", ""):
        return None
    update = cpe["update"]
    if update not in ("*", "-", ""):
        return f"{version}{update}"
    return version


def _rule_from_match(match: dict, requires: list[list[str]]) -> dict:
    cpe = parse_cpe(match.get("criteria"))
    rule = {
        "vendor": cpe["vendor"],
        "product": cpe["product"],
        "part": cpe["part"],
        "criteria": match.get("criteria"),
        "version": _exact_version(cpe),
        "start_including": match.get("versionStartIncluding"),
        "start_excluding": match.get("versionStartExcluding"),
        "end_including": match.get("versionEndIncluding"),
        "end_excluding": match.get("versionEndExcluding"),
        "requires": requires,
    }
    if cpe["version"] == "-":
        rule["version_not_applicable"] = True
    return {key: value for key, value in rule.items() if value not in (None, "")}


def rules_from_configurations(configurations) -> list[dict]:
    """Normalize NVD ``configurations`` into match rules.

    A configuration is an OR of its nodes unless ``operator == "AND"``: then the
    vulnerable CPEs only apply together with the platform CPEs of the other
    nodes (e.g. RouterOS only on specific hardware), kept as ``requires``.
    Negated nodes cannot be represented safely and make the record unparseable.
    """
    rules = []
    for configuration in configurations or []:
        nodes = configuration.get("nodes") or []
        if any(node.get("negate") for node in nodes):
            raise AdvisoryParseError("configurazione con nodi negati non supportata")
        vulnerable_nodes = [n for n in nodes if any(m.get("vulnerable") for m in n.get("cpeMatch") or [])]
        requires: list[list[str]] = []
        if (configuration.get("operator") or "OR").upper() == "AND":
            for node in nodes:
                if node in vulnerable_nodes:
                    continue
                group = []
                for match in node.get("cpeMatch") or []:
                    cpe = parse_cpe(match.get("criteria"))
                    group.append(cpe["product"])
                if group:
                    requires.append(group)
        for node in vulnerable_nodes:
            for match in node.get("cpeMatch") or []:
                if match.get("vulnerable"):
                    rules.append(_rule_from_match(match, requires))
    return rules


def _severity(metrics: dict) -> tuple[str, float | None]:
    for key in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        entries = metrics.get(key) or []
        if not entries:
            continue
        entry = next((item for item in entries if item.get("type") == "Primary"), entries[0])
        data = entry.get("cvssData") or {}
        severity = (data.get("baseSeverity") or entry.get("baseSeverity") or "").lower()
        score = data.get("baseScore")
        return (severity if severity in SEVERITIES else "unknown"), (float(score) if score is not None else None)
    return "unknown", None


def _timestamp(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise AdvisoryParseError(f"data non valida: {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _vendor_label(cpe_vendor: str) -> str:
    for data in vendor_cpe.BRANDS.values():
        if cpe_vendor in data["cpe_vendors"]:
            return data["label"]
    return cpe_vendor


def _product_label(product: str) -> str:
    text = product[:-9] if product.endswith("_firmware") else product
    return text.replace("_", " ").upper() if len(text) <= 12 else text.replace("_", " ").title()


def parse_nvd_item(item: dict) -> dict:
    cve = (item or {}).get("cve") or {}
    cve_id = str(cve.get("id") or "").strip()
    if not re.fullmatch(r"CVE-\d{4}-\d{4,}", cve_id):
        raise AdvisoryParseError(f"identificativo CVE mancante o non valido: {cve_id!r}")
    try:
        rules = rules_from_configurations(cve.get("configurations"))
    except AdvisoryParseError as exc:
        raise AdvisoryParseError(f"{cve_id}: {exc}") from exc
    severity, cvss = _severity(cve.get("metrics") or {})
    description = next(
        (d.get("value") for d in cve.get("descriptions") or [] if d.get("lang") == "en"),
        None,
    )
    references = cve.get("references") or []
    vendor_url = next((r.get("url") for r in references if "Vendor Advisory" in (r.get("tags") or [])), None)
    tracked = next(
        (r for r in rules if (r.get("vendor"), r.get("product")) in PRODUCT_LABELS),
        rules[0] if rules else None,
    )
    vendor, product = (None, None)
    if tracked:
        vendor, product = PRODUCT_LABELS.get((tracked["vendor"], tracked["product"]), (_vendor_label(tracked["vendor"]), _product_label(tracked["product"])))
    return {
        "cve_id": cve_id,
        "source_status": (cve.get("vulnStatus") or "").strip() or None,
        "summary": (description or "").strip()[:4000] or None,
        "severity": severity,
        "cvss": cvss,
        "published_at": _timestamp(cve.get("published")),
        "modified_at": _timestamp(cve.get("lastModified")),
        "vendor": vendor,
        "product": product,
        "vendor_advisory_url": (vendor_url or "")[:1000] or None,
        "nvd_url": NVD_DETAIL_URL + cve_id,
        "match_rules": rules,
    }


def parse_nvd_page(payload) -> tuple[list[dict], list[dict]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("vulnerabilities"), list):
        raise AdvisorySourceError("risposta NVD senza elenco 'vulnerabilities'")
    records, errors = [], []
    for item in payload["vulnerabilities"]:
        try:
            records.append(parse_nvd_item(item))
        except (AdvisoryParseError, TypeError, AttributeError, ValueError) as exc:
            cve_id = str(((item or {}).get("cve") or {}).get("id") or "?") if isinstance(item, dict) else "?"
            errors.append({"cve": cve_id, "error": str(exc)[:300]})
    return records, errors


# ---------------------------------------------------------------- fetching --

def _nvd_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+00:00")


def fetch_nvd(
    base_url: str,
    api_key: str | None,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep=time.sleep,
    cpes=None,
):
    """Yield parsed NVD pages for every tracked CPE."""
    headers = {"Accept": "application/json", "User-Agent": "nsm-platform advisory-sync"}
    if api_key:
        headers["apiKey"] = api_key
    pause = PAGE_PAUSE_SECONDS[bool(api_key)]
    first_request = True
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, headers=headers, transport=transport) as client:
        for cpe in cpes or TRACKED_CPES:
            start_index = 0
            for _page in range(MAX_PAGES):
                params = {"virtualMatchString": cpe, "resultsPerPage": RESULTS_PER_PAGE, "startIndex": start_index}
                if start and end:
                    params["lastModStartDate"] = _nvd_time(start)
                    params["lastModEndDate"] = _nvd_time(end)
                if not first_request:
                    sleep(pause)
                first_request = False
                try:
                    response = client.get(base_url, params=params)
                except httpx.TimeoutException as exc:
                    raise AdvisorySourceError("timeout verso NVD") from exc
                except httpx.HTTPError as exc:
                    raise AdvisorySourceError(f"errore di rete verso NVD: {exc.__class__.__name__}") from exc
                if response.status_code in (403, 429):
                    raise AdvisorySourceError(f"NVD ha rifiutato la richiesta (HTTP {response.status_code}, limite di frequenza o API key non valida)")
                if response.status_code >= 400:
                    raise AdvisorySourceError(f"NVD ha risposto HTTP {response.status_code}")
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise AdvisorySourceError("risposta NVD non in formato JSON") from exc
                yield payload
                total = int(payload.get("totalResults") or 0)
                count = len(payload.get("vulnerabilities") or [])
                start_index += count
                if count == 0 or start_index >= total:
                    break
            else:
                raise AdvisorySourceError(f"troppe pagine NVD per {cpe} (limite {MAX_PAGES})")


# --------------------------------------------------------------- ingestion --

_COMPARED_FIELDS = (
    "source_status", "summary", "severity", "cvss", "published_at", "modified_at",
    "vendor", "product", "vendor_advisory_url", "nvd_url", "match_rules",
)


def ingest_advisories(db, records: list[dict], now: datetime, source: str = PROVIDER) -> dict:
    """Upsert normalized advisories by CVE id. Caller commits."""
    stats = {"created": 0, "updated": 0, "unchanged": 0, "rejected": 0}
    if not records:
        return stats
    ids = [record["cve_id"] for record in records]
    existing = {a.cve_id: a for a in db.scalars(select(SecurityAdvisory).where(SecurityAdvisory.cve_id.in_(ids)))}
    for record in records:
        if (record.get("source_status") or "").lower() == "rejected":
            stats["rejected"] += 1
        advisory = existing.get(record["cve_id"])
        if advisory is None:
            advisory = SecurityAdvisory(cve_id=record["cve_id"], source=source, fetched_at=now)
            for key in _COMPARED_FIELDS:
                setattr(advisory, key, record.get(key))
            db.add(advisory)
            existing[record["cve_id"]] = advisory
            stats["created"] += 1
            continue
        changed = advisory.source != source or any(
            _normalized(getattr(advisory, key)) != _normalized(record.get(key)) for key in _COMPARED_FIELDS
        )
        advisory.fetched_at = now
        if not changed:
            stats["unchanged"] += 1
            continue
        advisory.source = source
        for key in _COMPARED_FIELDS:
            setattr(advisory, key, record.get(key))
        stats["updated"] += 1
    return stats


def _normalized(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(microsecond=0)
    return value


# ---------------------------------------------------------------- the run --

def connection_for(db) -> ConnectorIntegration | None:
    return db.scalar(select(ConnectorIntegration).where(ConnectorIntegration.provider == PROVIDER))


def sync_state(connection: ConnectorIntegration | None) -> dict:
    return dict(((connection.settings or {}) if connection else {}).get("sync") or {})


def interval_minutes(connection: ConnectorIntegration) -> int:
    raw = (connection.settings or {}).get("sync_interval_minutes", INTERVAL_DEFAULT_MINUTES)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = INTERVAL_DEFAULT_MINUTES
    return min(INTERVAL_MAX_MINUTES, max(INTERVAL_MIN_MINUTES, value))


def _store(connection: ConnectorIntegration, key: str, value: dict) -> None:
    settings = dict(connection.settings or {})
    settings[key] = value
    connection.settings = settings


def _parse_time(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def api_key(connection: ConnectorIntegration) -> str | None:
    if not connection.secret_encrypted:
        return None
    return decrypt_text(connection.secret_encrypted)


def run_advisory_sync(
    db,
    connection: ConnectorIntegration,
    now: datetime,
    *,
    trigger: str,
    transport: httpx.BaseTransport | None = None,
    sleep=time.sleep,
) -> dict:
    """One ingestion + matching cycle. Records health on ``connection``; caller commits."""
    state = sync_state(connection)
    state["last_attempt_at"] = now.isoformat()
    state["trigger"] = trigger
    cursor = _parse_time(state.get("cursor"))
    full = cursor is None or now - cursor > MAX_WINDOW or trigger == "full"
    start = None if full else cursor - CURSOR_OVERLAP
    totals = {"fetched": 0, "created": 0, "updated": 0, "unchanged": 0, "rejected": 0, "parse_errors": 0}
    parse_errors: list[dict] = []
    try:
        key = api_key(connection)
        cpes = vendor_cpe.tracked_cpes(db.scalars(select(Device)))
        known = set(state.get("tracked_cpes") or ([] if full else list(TRACKED_CPES)))
        added = [cpe for cpe in cpes if cpe not in known]
        # Products that just entered the inventory get their whole history once; the others stay incremental.
        passes = [(cpes, start, None if full else now)]
        if added and not full:
            passes.insert(0, (added, None, None))
        for pass_cpes, pass_start, pass_end in passes:
            for payload in fetch_nvd(
                connection.base_url or NVD_DEFAULT_URL,
                key,
                start=pass_start,
                end=pass_end,
                transport=transport,
                sleep=sleep,
                cpes=pass_cpes,
            ):
                records, errors = parse_nvd_page(payload)
                totals["fetched"] += len(records) + len(errors)
                totals["parse_errors"] += len(errors)
                parse_errors.extend(errors)
                for name, value in ingest_advisories(db, records, now).items():
                    totals[name] += value
                db.flush()
        state["tracked_cpes"] = cpes
    except (AdvisorySourceError, ValueError) as exc:
        db.rollback()
        failures = int(state.get("consecutive_failures") or 0) + 1
        backoff = min(timedelta(minutes=interval_minutes(connection)) * (2 ** (failures - 1)), MAX_BACKOFF)
        state.update(
            {
                "last_status": "failed",
                "last_error": str(exc)[:500],
                "consecutive_failures": failures,
                "next_attempt_at": (now + backoff).isoformat(),
            }
        )
        _store(connection, "sync", state)
        connection.last_error = str(exc)[:500]
        core.add_event(
            db,
            "SECURITY_ADVISORY_SYNC_FAILED",
            details={"source": PROVIDER, "error": str(exc)[:500], "consecutive_failures": failures, "trigger": trigger},
            severity="warning",
            result="failed",
            source=PROVIDER,
        )
        if failures >= CONNECTOR_ISSUE_AFTER_FAILURES:
            _open_issue(
                db,
                CONNECTOR_ISSUE_TITLE,
                "integration",
                "high",
                {"message": f"Aggiornamento advisory NVD fallito {failures} volte consecutive: {str(exc)[:300]}", "consecutive_failures": failures},
                source_url="/admin/integrations/nvd",
            )
        return {"status": "failed", "error": str(exc), "consecutive_failures": failures}

    match_stats = reconcile_advisory_matches(db, now)
    db.flush()
    match_stats.update(housekeeping(db, now))
    state.update(
        {
            "last_status": "success",
            "last_error": None,
            "consecutive_failures": 0,
            "last_success_at": now.isoformat(),
            "cursor": now.isoformat(),
            "last_mode": "full" if full else "incremental",
            "next_attempt_at": (now + timedelta(minutes=interval_minutes(connection))).isoformat(),
            "last_stats": totals,
            "parse_errors": parse_errors[:MAX_PARSE_ERRORS_KEPT],
        }
    )
    _store(connection, "sync", state)
    _store(connection, "matching", {"last_run_at": now.isoformat(), "last_stats": match_stats})
    connection.last_error = None
    connection.last_sync_at = now
    _resolve_issue(db, CONNECTOR_ISSUE_TITLE, "integration", now)
    changed = totals["created"] or totals["updated"] or match_stats["opened"] or match_stats["reopened"] or match_stats["resolved"]
    if trigger != "schedule" or changed or totals["parse_errors"]:
        core.add_event(
            db,
            "SECURITY_ADVISORIES_SYNCED",
            details={"source": PROVIDER, "mode": state["last_mode"], "trigger": trigger, **totals, "matching": match_stats},
            severity="warning" if totals["parse_errors"] else "info",
            source=PROVIDER,
        )
    return {"status": "success", **totals, "matching": match_stats}


def run_matching(db, connection: ConnectorIntegration, now: datetime, *, trigger: str) -> dict:
    stats = reconcile_advisory_matches(db, now)
    db.flush()
    stats.update(housekeeping(db, now))
    _store(connection, "matching", {"last_run_at": now.isoformat(), "last_stats": stats, "trigger": trigger})
    if stats["opened"] or stats["reopened"] or stats["resolved"] or trigger == "manual":
        core.add_event(db, "SECURITY_MATCHING_COMPLETED", details={"trigger": trigger, **stats}, source=PROVIDER)
    return stats


def sync_security_advisories(now: datetime | None = None) -> dict:
    """Worker entry point: ingest when due, otherwise keep findings fresh."""
    now = now or utcnow()
    with SessionLocal() as db:
        connection = connection_for(db)
        if not connection:
            return {"status": "not_configured"}
        state = sync_state(connection)
        next_attempt = _parse_time(state.get("next_attempt_at"))
        if connection.is_enabled and (next_attempt is None or next_attempt <= now):
            result = run_advisory_sync(db, connection, now, trigger="schedule")
            db.commit()
            return result
        last_match = _parse_time(((connection.settings or {}).get("matching") or {}).get("last_run_at"))
        if last_match is None or now - last_match >= MATCH_INTERVAL:
            stats = run_matching(db, connection, now, trigger="schedule")
            db.commit()
            return {"status": "matched", "matching": stats}
        return {"status": "not_due"}
