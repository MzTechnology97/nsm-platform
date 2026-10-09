"""Availability measured by the ICMP monitor (ping from NSM) for reports and customer pages.

A round of the ICMP monitor (3 echoes every 2 minutes) is *up* when at least
one echo got a reply.  Availability is the share of rounds that were up in the
period, weighted by the rounds each stored row represents: consolidated rows
(10-minute points after 7 days) count ``sent / 3`` rounds, with the replied
rounds estimated as ``ceil(received / 3)``.  Outages are runs of consecutive
rounds without any reply, measured on the full-resolution samples.

Only devices with ICMP samples in the period are listed: without the monitor
NSM has no measure of availability and never invents one.
"""
from __future__ import annotations

import math
from datetime import datetime

from sqlalchemy import select

from app.agent_models import DevicePingSample
from app.icmp_monitor import COUNT
from app.models import Device

TARGET_PERCENT = 99.0
ROUND_MINUTES = 2


def _name(device: Device) -> str:
    return device.display_name or device.device_identity or device.name


def device_availability(samples: list) -> dict | None:
    rounds = up = 0.0
    sent = received = 0
    rtts = []
    outages, current, longest, down_rounds = 0, 0, 0, 0
    for sample in samples:
        weight = max(1.0, sample.sent / COUNT)
        replied = min(weight, math.ceil(sample.received / COUNT)) if sample.received else 0
        rounds += weight
        up += replied
        sent += sample.sent
        received += sample.received
        if sample.rtt_avg is not None:
            rtts.append(sample.rtt_avg)
        if sample.received == 0:
            current += weight
            down_rounds += weight
            if current == weight:
                outages += 1
            longest = max(longest, current)
        else:
            down_rounds += weight - replied
            current = 0
    if not rounds:
        return None
    return {
        "rounds": int(rounds),
        "availability": round(100.0 * up / rounds, 3),
        "downtime_minutes": int(down_rounds * ROUND_MINUTES),
        "outages": outages,
        "longest_outage_minutes": int(longest * ROUND_MINUTES),
        "loss": round(100.0 * (1 - received / sent), 2) if sent else None,
        "rtt_avg": round(sum(rtts) / len(rtts), 1) if rtts else None,
    }


def report_section(db, device_ids, lower: datetime, upper: datetime) -> dict:
    """Per-device availability in the period, worst first, and fleet figures."""
    if not device_ids:
        return {"devices": [], "measured": 0, "below_target": 0, "average": None, "target": TARGET_PERCENT}
    samples: dict = {}
    for sample in db.scalars(select(DevicePingSample).where(DevicePingSample.device_id.in_(list(device_ids)), DevicePingSample.observed_at >= lower,
                                                           DevicePingSample.observed_at < upper)
                             .order_by(DevicePingSample.device_id, DevicePingSample.observed_at)):
        samples.setdefault(sample.device_id, []).append(sample)
    devices = {d.id: d for d in db.scalars(select(Device).where(Device.id.in_(list(samples))))} if samples else {}
    rows = []
    for device_id, rows_for_device in samples.items():
        measure = device_availability(rows_for_device)
        device = devices.get(device_id)
        if measure is None or device is None:
            continue
        rows.append({"device_id": str(device_id), "device": _name(device), "customer": device.customer.name if device.customer else "—", **measure})
    rows.sort(key=lambda r: (r["availability"], -r["downtime_minutes"], r["device"]))
    total_rounds = sum(r["rounds"] for r in rows)
    average = round(sum(r["availability"] * r["rounds"] for r in rows) / total_rounds, 3) if total_rounds else None
    return {"devices": rows, "measured": len(rows), "below_target": sum(1 for r in rows if r["availability"] < TARGET_PERCENT),
            "average": average, "target": TARGET_PERCENT}


def render_report_section(doc, section: dict, number: int) -> None:
    doc.heading(f"{number}. Disponibilità (ping da NSM)", 2)
    if not section.get("measured"):
        doc.paragraph("Nessun apparato con monitoraggio ICMP nel periodo: la disponibilità non è misurata. "
                      "Si attiva per apparato o per cliente da Operazioni > Monitoring.")
        return
    doc.key_values([
        ("Apparati misurati", section["measured"]),
        ("Disponibilità media (ponderata sui controlli)", f"{section['average']:.2f}%"),
        (f"Apparati sotto il {section['target']:g}%", section["below_target"]),
    ])
    doc.table(
        ["Apparato", "Cliente", "Disponibilità", "Fermo (min)", "Interruzioni", "Più lunga (min)", "Perdita", "RTT medio"],
        [[r["device"], r["customer"], f"{r['availability']:.2f}%", r["downtime_minutes"], r["outages"], r["longest_outage_minutes"],
          f"{r['loss']:.1f}%" if r["loss"] is not None else "—", f"{r['rtt_avg']} ms" if r["rtt_avg"] is not None else "—"]
         for r in section["devices"][:200]],
        [105, 85, 55, 50, 50, 55, 50, 61],
    )
    doc.paragraph("Un controllo (3 echo ogni 2 minuti dal server NSM) è considerato riuscito se almeno una risposta arriva. "
                  "La misura vale dal punto di vista di NSM: comprende il percorso di rete fino all'apparato.", size=8.5, gray=0.35)
