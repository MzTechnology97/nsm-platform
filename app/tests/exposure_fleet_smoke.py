"""SCAN-01: fleet view of exposed services (Security → Esposizione) and exposure lines in the security newsletter."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app import device_exposure as expo
from app import external_exposure as ext
from app import notification_digest as digest
from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, Customer, Device, User, utcnow
from app.report_builder import collect_report_data, render_pdf
from app.security import hash_password

PASSWORD = "CI-Exposure-Fleet-2026"
PENDING_IP = "203.0.113.90"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    ext._public = lambda value: str(value or "").strip() == PENDING_IP
    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        customer = Customer(name=f"CI Fleet {suffix}", code=f"FL{suffix}")
        other = Customer(name=f"CI Fleet other {suffix}", code=f"FO{suffix}")
        db.add_all([customer, other])
        db.flush()
        router = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-FL-RTR-{suffix}", status="online",
                        inventory_data={"agent_transport": "modern", "agent_version": "0.49.14", "exposure": {
                            "checked_at": now.isoformat(), "source": "mikrotik_agent", "exposed": 1, "worst": "high",
                            "findings": [{"service": "winbox", "label": "winbox", "port": 8291, "proto": "tcp", "state": "exposed", "severity": "high"},
                                         {"service": "ssh", "label": "ssh", "port": 22, "proto": "tcp", "state": "protected", "severity": "info"}],
                            "port_forwards": [{"protocol": "tcp", "public_ports": "8080", "to_address": "192.0.2.20", "sensitive": ["HTTP"], "certain": True, "severity": "high"}]}})
        clean = Device(customer_id=customer.id, vendor="ubiquiti", device_type="wireless_cpe", name=f"TEST-FL-UBNT-{suffix}", status="online",
                       inventory_data={"exposure": {"checked_at": now.isoformat(), "source": "external", "target_ip": "198.51.100.7", "exposed": 0,
                                                    "findings": [{"service": "ssh", "label": "SSH", "port": 22, "proto": "tcp", "state": "closed", "severity": "info"}]}})
        setup = Device(customer_id=customer.id, vendor="tp-link", device_type="cpe", name=f"TEST-FL-TPL-{suffix}", management_ip="192.0.2.30", status="online")
        pending = Device(customer_id=other.id, vendor="generic", device_type="cpe", name=f"TEST-FL-GEN-{suffix}", management_ip=PENDING_IP, status="online")
        db.add_all([router, clean, setup, pending])
        db.add(User(username=f"ci-fl-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True))
        db.flush()
        db.add(ActionIssue(category=expo.ISSUE_CATEGORY, severity="warning", status="open", title=expo.ISSUE_TITLE, customer_id=customer.id, device_id=router.id,
                           details={"services": ["winbox tcp/8291", "port forward HTTP verso 192.0.2.20 tcp/8080"], "checked_at": now.isoformat()}))
        db.commit()
        ids = {"router": router.id, "clean": clean.id, "setup": setup.id, "pending": pending.id, "customer": customer.id}

        data = expo.fleet(db, customer.id)
        states = {r["device"].id: r["state"] for r in data["rows"]}
        assert states == {ids["router"]: "exposed", ids["clean"]: "clean", ids["setup"]: "setup"}, states
        top = data["rows"][0]
        assert top["device"].id == ids["router"] and top["worst"] == "high" and len(top["exposed"]) == 2, "winbox + sensitive port forward"
        assert data["counts"] == {"exposed": 1, "clean": 1, "setup": 1, "pending": 0, "critical": 0, "total": 3}
        assert expo.fleet(db)["counts"]["pending"] >= 1

        # Newsletter: the exposure lines are added (or sent alone when there are no new CVEs).
        lines, count, critical = digest.exposure_lines(db, now - timedelta(minutes=5), now + timedelta(minutes=5))
        assert count == 1 and not critical and any(f"TEST-FL-RTR-{suffix}: winbox tcp/8291" in line for line in lines)
        content = digest.digest_content(db, now - timedelta(minutes=5), now + timedelta(minutes=5))
        assert content and "servizi esposti" in content["title"] and "Security → Esposizione" in content["body"]
        assert digest.exposure_lines(db, now + timedelta(minutes=5), now + timedelta(minutes=10))[1] == 0, "only issues opened in the period"

        # Evidence report, section 3: current exposure state of the customer's devices.
        report = collect_report_data(db, customer=db.get(Customer, customer.id), period_start=(now - timedelta(days=1)).date(), period_end=now.date())
        assert report["exposure"]["counts"]["exposed"] == 1 and report["exposure"]["rows"][0]["services"].startswith("winbox tcp/8291")
        pdf = render_pdf(report, report_id="TEST-EXP", generated_at=now, generated_by="ci", platform_name="NSM").decode("latin-1")
        for marker in ("3. Vulnerabilit", "Servizi di gestione esposti su Internet", f"TEST-FL-RTR-{suffix}", "4. Ciclo di vita", "9. Compliance"):
            assert marker in pdf, marker

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-fl-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    page = client.get(f"/security/exposure?customer={ids['customer']}").text
    assert "Servizi esposti su Internet" in page and f"TEST-FL-RTR-{suffix}" in page and "winbox tcp/8291" in page and "port forward HTTP" in page
    assert f"TEST-FL-UBNT-{suffix}" not in page, "default view lists only exposed devices"
    assert 'href="/security/exposure"' in page, "sidebar entry"
    setup_page = client.get(f"/security/exposure?view=setup&customer={ids['customer']}").text
    assert f"TEST-FL-TPL-{suffix}" in setup_page and "serve l&#39;IP pubblico" in setup_page
    everything = client.get(f"/security/exposure?view=all&customer={ids['customer']}").text
    assert all(f"TEST-FL-{k}-{suffix}" in everything for k in ("RTR", "UBNT", "TPL")) and f"TEST-FL-GEN-{suffix}" not in everything
    assert "verifica in corso" in client.get("/security/exposure?view=all").text
    assert client.get("/security/exposure?view=bogus&customer=not-a-uuid").status_code == 200
    print("Exposure fleet smoke passed")


if __name__ == "__main__":
    main()
