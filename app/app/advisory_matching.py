"""Device ↔ advisory matching (SEC-02).

Advisories carry normalized applicability rules (``SecurityAdvisory.match_rules``)
produced by a source adapter (see ``advisory_sources``). This module only reads
those rules; it never knows which source produced them.

Rule shape (JSON)::

    {
      "vendor": "mikrotik", "product": "routeros", "part": "o",
      "criteria": "<original CPE match string>",
      "version": "6.49.6" | null,              # exact version, or
      "start_including": ..., "start_excluding": ...,
      "end_including": ..., "end_excluding": ...,  # range bounds
      "requires": [["rb750gr3", "rb760igs"], ...] # hardware: one per group
    }

A Device is only evaluated when NSM knows its product (MikroTik → RouterOS).
A rule never matches on brand alone:

* no version on the Device, an unparseable version, a rule with no version and
  no bounds, or a hardware constraint with an unknown model → ``unknown``;
* a different product or an unmet hardware constraint → ``not_applicable``;
* otherwise the version comparison decides ``affected`` / ``not_affected``.

Only ``affected`` creates or keeps an open ``DeviceVulnerability``; findings
that stop matching (e.g. after an upgrade) are resolved with evidence, and
``unknown`` Devices are listed explicitly instead of being counted as exposed.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import Device, DeviceVulnerability, SecurityAdvisory
from app.routeros_version import compare_routeros_versions, parse_routeros_version

AFFECTED = "affected"
NOT_AFFECTED = "not_affected"
NOT_APPLICABLE = "not_applicable"
UNKNOWN = "unknown"

# Sources whose findings this module owns. Manually entered advisories are
# never resolved or reopened automatically.
MATCHED_SOURCES = ("nvd",)
REJECTED_STATUSES = ("rejected",)

_RANK = {NOT_APPLICABLE: 0, NOT_AFFECTED: 1, UNKNOWN: 2, AFFECTED: 3}


@dataclass(frozen=True)
class DeviceProfile:
    vendor: str
    product: str | None
    version: str | None
    model_key: str | None


@dataclass
class Assessment:
    state: str
    confidence: str | None = None
    reason: str = ""
    rule: dict | None = None
    fixed_version: str | None = None
    evidence: dict = field(default_factory=dict)


def model_key(value: str | None) -> str | None:
    """Normalize a hardware model for comparison: ``hAP ax2`` → ``hapax2``."""
    key = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
    return key or None


def device_profile(device: Device) -> DeviceProfile:
    vendor = (device.vendor or "").strip().lower()
    product = "routeros" if vendor == "mikrotik" else None
    version = (device.firmware_version or "").strip() or None
    return DeviceProfile(vendor=vendor, product=product, version=version, model_key=model_key(device.model))


def _version_check(rule: dict, installed) -> tuple[str, str, str | None]:
    """Return (state, reason, fixed_version) for a rule whose product already matched."""
    exact = rule.get("version")
    bounds = {key: rule.get(key) for key in ("start_including", "start_excluding", "end_including", "end_excluding")}
    if exact:
        result = compare_routeros_versions(installed, exact)
        if result is None:
            return UNKNOWN, "versione della regola non interpretabile", None
        return (AFFECTED, f"versione {exact}", None) if result == 0 else (NOT_AFFECTED, f"solo versione {exact}", None)
    if not any(bounds.values()):
        return UNKNOWN, "la fonte non indica versioni affette", None
    for key, value in bounds.items():
        if value and parse_routeros_version(value) is None:
            return UNKNOWN, f"limite {key} non interpretabile", None

    def cmp(bound):
        return compare_routeros_versions(installed, bound)

    if bounds["start_including"] and cmp(bounds["start_including"]) < 0:
        return NOT_AFFECTED, f"precedente a {bounds['start_including']}", None
    if bounds["start_excluding"] and cmp(bounds["start_excluding"]) <= 0:
        return NOT_AFFECTED, f"non successiva a {bounds['start_excluding']}", None
    if bounds["end_excluding"] and cmp(bounds["end_excluding"]) >= 0:
        return NOT_AFFECTED, f"corretta da {bounds['end_excluding']}", bounds["end_excluding"]
    if bounds["end_including"] and cmp(bounds["end_including"]) > 0:
        return NOT_AFFECTED, f"successiva a {bounds['end_including']}", None
    return AFFECTED, describe_rule(rule), bounds["end_excluding"]


def evaluate_rule(rule: dict, profile: DeviceProfile) -> Assessment:
    if not profile.product:
        return Assessment(NOT_APPLICABLE, reason="prodotto dell'apparato non mappato")
    if (rule.get("vendor") or "").lower() != profile.vendor or (rule.get("product") or "").lower() != profile.product:
        return Assessment(NOT_APPLICABLE, reason="prodotto diverso")
    for group in rule.get("requires") or []:
        keys = {model_key(item) for item in group if model_key(item)}
        if not keys:
            continue
        if not profile.model_key:
            return Assessment(UNKNOWN, reason="regola limitata a modelli specifici, modello apparato sconosciuto", rule=rule)
        if profile.model_key not in keys:
            return Assessment(NOT_APPLICABLE, reason="modello hardware non incluso", rule=rule)
    if not profile.version:
        return Assessment(UNKNOWN, reason="versione installata sconosciuta", rule=rule)
    if parse_routeros_version(profile.version) is None:
        return Assessment(UNKNOWN, reason=f"versione installata non interpretabile ({profile.version})", rule=rule)
    state, reason, fixed = _version_check(rule, profile.version)
    if state == UNKNOWN:
        return Assessment(UNKNOWN, reason=reason, rule=rule)
    confidence = None
    if state == AFFECTED:
        open_ended = not rule.get("version") and not (rule.get("end_excluding") or rule.get("end_including"))
        confidence = "medium" if open_ended else "high"
    return Assessment(state, confidence=confidence, reason=reason, rule=rule, fixed_version=fixed)


def assess(rules: list[dict], profile: DeviceProfile) -> Assessment:
    """Combine every rule of one advisory: any affected rule wins, then unknown."""
    best = Assessment(NOT_APPLICABLE, reason="nessuna regola applicabile")
    for rule in rules or []:
        current = evaluate_rule(rule, profile)
        if _RANK[current.state] > _RANK[best.state] or (
            current.state == best.state == AFFECTED and current.confidence == "high" and best.confidence != "high"
        ):
            best = current
    if best.state == AFFECTED:
        best.evidence = {
            "rule": describe_rule(best.rule or {}),
            "criteria": (best.rule or {}).get("criteria"),
            "installed_version": profile.version,
            "reason": best.reason,
        }
    return best


def describe_rule(rule: dict) -> str:
    """Human-readable range: ``≥ 6.40 e < 6.49.7`` / ``= 7.1rc1`` / ``tutte``."""
    if rule.get("version"):
        text = f"= {rule['version']}"
    else:
        parts = []
        if rule.get("start_including"):
            parts.append(f"≥ {rule['start_including']}")
        if rule.get("start_excluding"):
            parts.append(f"> {rule['start_excluding']}")
        if rule.get("end_excluding"):
            parts.append(f"< {rule['end_excluding']}")
        if rule.get("end_including"):
            parts.append(f"≤ {rule['end_including']}")
        text = " e ".join(parts) if parts else "versione non specificata"
    hardware = [", ".join(group) for group in rule.get("requires") or [] if group]
    if hardware:
        text += " · solo " + " / ".join(hardware)
    return text


def _rejected(advisory: SecurityAdvisory) -> bool:
    return (advisory.source_status or "").strip().lower() in REJECTED_STATUSES


def reconcile_advisory_matches(db, now: datetime, advisory_ids=None) -> dict:
    """Bring ``DeviceVulnerability`` rows of source-managed advisories in line with the rules.

    Devices are grouped by (vendor, version, model) so each advisory is evaluated
    once per distinct profile. Caller commits.
    """
    stats = {"advisories": 0, "opened": 0, "reopened": 0, "updated": 0, "resolved": 0, "unknown_assessments": 0}
    query = select(SecurityAdvisory).where(SecurityAdvisory.source.in_(MATCHED_SOURCES))
    if advisory_ids is not None:
        query = query.where(SecurityAdvisory.id.in_(list(advisory_ids)))
    advisories = list(db.scalars(query))
    if not advisories:
        return stats

    devices = list(db.scalars(select(Device)))
    groups: dict[DeviceProfile, list[Device]] = defaultdict(list)
    for device in devices:
        profile = device_profile(device)
        if profile.product:
            groups[profile].append(device)

    existing = defaultdict(dict)
    for row in db.scalars(
        select(DeviceVulnerability).where(DeviceVulnerability.advisory_id.in_([a.id for a in advisories]))
    ):
        existing[row.advisory_id][row.device_id] = row

    for advisory in advisories:
        stats["advisories"] += 1
        rows = existing.get(advisory.id, {})
        if _rejected(advisory):
            for row in rows.values():
                if row.status != "resolved":
                    _resolve(row, now, "advisory_rejected", None)
                    stats["resolved"] += 1
            continue
        for profile, members in groups.items():
            result = assess(advisory.match_rules or [], profile)
            if result.state == UNKNOWN:
                stats["unknown_assessments"] += len(members)
            for device in members:
                row = rows.get(device.id)
                if result.state == AFFECTED:
                    evidence = {**result.evidence, "source": advisory.source, "evaluated_at": now.isoformat()}
                    if row is None:
                        db.add(
                            DeviceVulnerability(
                                advisory_id=advisory.id,
                                device_id=device.id,
                                status="open",
                                detected_at=now,
                                installed_version=profile.version,
                                fixed_version=result.fixed_version,
                                evidence=evidence,
                                confidence=result.confidence,
                                last_evaluated_at=now,
                            )
                        )
                        stats["opened"] += 1
                        continue
                    if row.status == "resolved":
                        row.status = "open"
                        row.resolved_at = None
                        evidence["reopened_at"] = now.isoformat()
                        stats["reopened"] += 1
                    elif (
                        row.installed_version != profile.version
                        or row.fixed_version != result.fixed_version
                        or row.confidence != result.confidence
                    ):
                        stats["updated"] += 1
                    row.installed_version = profile.version
                    row.fixed_version = result.fixed_version
                    row.confidence = result.confidence
                    row.evidence = evidence
                    row.last_evaluated_at = now
                elif row is not None and row.status != "resolved" and result.state in (NOT_AFFECTED, NOT_APPLICABLE):
                    _resolve(row, now, "no_longer_matches", profile.version, result.reason)
                    stats["resolved"] += 1
                elif row is not None:
                    row.last_evaluated_at = now
        # Findings for Devices that left the evaluated perimeter (vendor changed).
        evaluated = {device.id for members in groups.values() for device in members}
        for device_id, row in rows.items():
            if device_id not in evaluated and row.status != "resolved":
                _resolve(row, now, "device_not_evaluable", None, "apparato non più valutabile per questa fonte")
                stats["resolved"] += 1
    return stats


def _resolve(row: DeviceVulnerability, now: datetime, reason: str, version, detail: str | None = None) -> None:
    row.status = "resolved"
    row.resolved_at = now
    row.last_evaluated_at = now
    row.evidence = {
        **(row.evidence or {}),
        "resolution": reason,
        "resolution_detail": detail,
        "resolved_version": version,
        "resolved_at": now.isoformat(),
    }


def unknown_devices(db, advisory: SecurityAdvisory, customer_id=None) -> list[tuple[Device, str]]:
    """Devices of the advisory's product that cannot be assessed, with the reason."""
    if not advisory.match_rules:
        return []
    query = select(Device).where(Device.vendor.ilike("mikrotik")).options(selectinload(Device.customer))
    if customer_id:
        query = query.where(Device.customer_id == customer_id)
    out = []
    for device in db.scalars(query):
        profile = device_profile(device)
        if not profile.product:
            continue
        result = assess(advisory.match_rules, profile)
        if result.state == UNKNOWN:
            out.append((device, result.reason))
    return out
