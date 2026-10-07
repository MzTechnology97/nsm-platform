"""SEC-02: version-range matching, explicit unknown states, no brand-only matches."""
import uuid
from datetime import timedelta

from app.advisory_matching import (
    AFFECTED,
    NOT_AFFECTED,
    NOT_APPLICABLE,
    UNKNOWN,
    DeviceProfile,
    assess,
    describe_rule,
    model_key,
    reconcile_advisory_matches,
    unknown_devices,
)
from app.db import SessionLocal
from app.models import Customer, Device, DeviceVulnerability, SecurityAdvisory, utcnow

RANGE = {"vendor": "mikrotik", "product": "routeros", "start_including": "6.40", "end_excluding": "6.49.7"}
EXACT = {"vendor": "mikrotik", "product": "routeros", "version": "7.1rc1"}
HW_ONLY = {"vendor": "mikrotik", "product": "routeros", "end_excluding": "7.13", "requires": [["rb750gr3", "rb760igs"]]}
NO_VERSION = {"vendor": "mikrotik", "product": "routeros"}


def ros(version, model="RB5009UG+S+"):
    return DeviceProfile(vendor="mikrotik", product="routeros", version=version, model_key=model_key(model))


def pure_checks():
    assert assess([RANGE], ros("6.48.6")).state == AFFECTED
    assert assess([RANGE], ros("6.48.6")).fixed_version == "6.49.7"
    assert assess([RANGE], ros("6.48.6")).confidence == "high"
    assert assess([RANGE], ros("6.49.7")).state == NOT_AFFECTED, "end_excluding is the fixed release"
    assert assess([RANGE], ros("6.39.3")).state == NOT_AFFECTED
    assert assess([RANGE], ros("7.19.4 (stable)")).state == NOT_AFFECTED
    # Pre-releases sort before the release they precede.
    assert assess([RANGE], ros("6.49.7rc1")).state == AFFECTED
    assert assess([EXACT], ros("7.1rc1")).state == AFFECTED
    assert assess([EXACT], ros("7.1")).state == NOT_AFFECTED

    # Unknown, never exposed: no installed version, garbage version, rule without versions.
    assert assess([RANGE], ros(None)).state == UNKNOWN
    assert assess([RANGE], ros("unknown-build")).state == UNKNOWN
    assert assess([NO_VERSION], ros("7.19.4")).state == UNKNOWN, "no brand/product-only match"

    # Hardware-limited rules need the model.
    assert assess([HW_ONLY], ros("7.12.1", "RB750Gr3")).state == AFFECTED
    assert assess([HW_ONLY], ros("7.12.1", "hAP ax2")).state == NOT_APPLICABLE
    assert assess([HW_ONLY], ros("7.12.1", None)).state == UNKNOWN

    # Other vendors are not evaluated at all.
    other = DeviceProfile(vendor="ubiquiti", product=None, version="6.6.0", model_key="lbe5ac")
    assert assess([RANGE], other).state == NOT_APPLICABLE

    # Any affected rule wins over unknown ones.
    assert assess([NO_VERSION, RANGE], ros("6.45")).state == AFFECTED
    assert describe_rule(RANGE) == "≥ 6.40 e < 6.49.7"
    assert describe_rule(HW_ONLY) == "< 7.13 · solo rb750gr3, rb760igs"


def db_checks():
    suffix = uuid.uuid4().hex[:8]
    now = utcnow()
    with SessionLocal() as db:
        customer = Customer(name=f"CI Match {suffix}", code=f"MA{suffix[:6]}")
        db.add(customer)
        db.flush()
        old = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-MATCH-OLD", firmware_version="6.48.6", model="RB750Gr3")
        new = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-MATCH-NEW", firmware_version="7.19.4", model="RB750Gr3")
        blind = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="TEST-MATCH-BLIND")
        ubnt = Device(customer_id=customer.id, vendor="ubiquiti", device_type="cpe", name="TEST-MATCH-UBNT", firmware_version="6.48.6")
        nvd = SecurityAdvisory(cve_id=f"CVE-2099-{int(suffix, 16) % 90000 + 10000}", source="nvd", severity="high", cvss=8.1, match_rules=[RANGE])
        manual = SecurityAdvisory(cve_id=f"CVE-2098-{int(suffix, 16) % 90000 + 10000}", source="manual", severity="low")
        db.add_all([old, new, blind, ubnt, nvd, manual])
        db.flush()
        # A manual finding must never be touched by the matcher.
        db.add(DeviceVulnerability(advisory_id=manual.id, device_id=new.id, status="open"))
        db.flush()

        stats = reconcile_advisory_matches(db, now, [nvd.id, manual.id])
        db.commit()
        assert stats["opened"] == 1, stats
        rows = {r.device_id: r for r in db.query(DeviceVulnerability).filter_by(advisory_id=nvd.id)}
        assert set(rows) == {old.id}, "only the device inside the range, never by brand"
        finding = rows[old.id]
        assert finding.status == "open" and finding.fixed_version == "6.49.7" and finding.confidence == "high"
        assert finding.evidence["rule"] == "≥ 6.40 e < 6.49.7" and finding.evidence["installed_version"] == "6.48.6"
        unknown = unknown_devices(db, nvd)
        assert [d.name for d, _ in unknown if d.customer_id == customer.id] == ["TEST-MATCH-BLIND"]

        # Idempotent: a second run changes nothing.
        again = reconcile_advisory_matches(db, now + timedelta(minutes=1), [nvd.id])
        assert again["opened"] == again["reopened"] == again["resolved"] == again["updated"] == 0, again

        # Upgrade → resolved with evidence; downgrade → reopened.
        old.firmware_version = "6.49.7"
        stats = reconcile_advisory_matches(db, now + timedelta(minutes=2), [nvd.id])
        db.commit()
        db.refresh(finding)
        assert stats["resolved"] == 1 and finding.status == "resolved"
        assert finding.evidence["resolution"] == "no_longer_matches" and finding.evidence["resolved_version"] == "6.49.7"
        old.firmware_version = "6.45"
        stats = reconcile_advisory_matches(db, now + timedelta(minutes=3), [nvd.id])
        db.commit()
        db.refresh(finding)
        assert stats["reopened"] == 1 and finding.status == "open" and finding.resolved_at is None

        # A rejected advisory closes its findings.
        nvd.source_status = "Rejected"
        stats = reconcile_advisory_matches(db, now + timedelta(minutes=4), [nvd.id])
        db.commit()
        db.refresh(finding)
        assert stats["resolved"] == 1 and finding.evidence["resolution"] == "advisory_rejected"

        manual_row = db.query(DeviceVulnerability).filter_by(advisory_id=manual.id).one()
        assert manual_row.status == "open" and manual_row.last_evaluated_at is None


def main():
    pure_checks()
    db_checks()
    print("Advisory matching smoke passed")


if __name__ == "__main__":
    main()
