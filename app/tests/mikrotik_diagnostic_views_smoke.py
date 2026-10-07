"""MTK-05: structured diagnostic results (modern data and legacy :tostr output) and per-target history."""
import re
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient

from app.agent_models import DeviceJob
from app.db import SessionLocal
from app.entrypoint import app
from app.mikrotik_diagnostic_views import duration_ms, parse_tostr, ping_view, summary, traceroute_view
from app.models import Customer, Device, User, utcnow
from app.security import hash_password

PASSWORD = "CI-Diagnostic-Views-2026"
PING = [{"host": "192.0.2.1", "seq": "0", "size": "56", "ttl": "64", "time": "10ms"},
        {"host": "192.0.2.1", "seq": "1", "size": "56", "ttl": "64", "time": "12ms500us"},
        {"host": "192.0.2.1", "seq": "2", "status": "timeout"},
        {"host": "192.0.2.1", "seq": "3", "size": "56", "ttl": "64", "time": "14ms"}]
TRACE = [{"address": "198.51.100.1", "loss": "0%", "sent": "1", "last": "1.2ms", "avg": "1.2ms", "best": "1.2ms", "worst": "1.2ms"},
         {"address": "", "loss": "100%", "sent": "1", "status": "timeout"},
         {"address": "192.0.2.1", "loss": "0", "sent": "1", "last": "9ms", "avg": "9ms", "best": "9ms", "worst": "9ms"}]
LEGACY_LOGS = ".id=*1;message=login failure for user admin from 203.0.113.9 via ssh;time=10:00:01;topics=system;error;critical;.id=*2;message=ether2 link down;time=10:01:00;topics=interface;warning"


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    assert duration_ms("12ms500us") == 12.5 and duration_ms("1s5ms") == 1005 and duration_ms("00:00:00.012") == 12
    assert duration_ms("350us") == 0.35 and duration_ms(7) == 7 and duration_ms("n/a") is None
    rows = parse_tostr(LEGACY_LOGS)
    assert len(rows) == 2 and rows[0]["topics"] == "system,error,critical" and rows[1]["message"] == "ether2 link down"
    assert parse_tostr("host=192.0.2.1;seq=0;time=1ms;host=192.0.2.1;seq=1;time=2ms")[1]["seq"] == "1", "repeated keys start a new row"
    ping = ping_view(PING)
    assert (ping["sent"], ping["received"], ping["loss"], ping["min"], ping["max"]) == (4, 3, 25.0, 10.0, 14.0)
    assert ping["avg"] == 12.17 and ping["jitter"] == 2.0
    trace = traceroute_view(TRACE)
    assert trace["count"] == 3 and trace["reached"] and trace["hops"][1]["silent"] and trace["hops"][1]["address"] == "*"

    suffix = uuid.uuid4().hex[:6]
    now = utcnow()
    with SessionLocal() as db:
        user = User(username=f"ci-dv-{suffix}", password_hash=hash_password(PASSWORD), role="admin", is_active=True)
        customer = Customer(name=f"CI Diagnostic Views {suffix}", code=f"DV{suffix}")
        db.add_all([user, customer])
        db.flush()
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-DV-{suffix}", status="online",
                        firmware_version="7.24.4", inventory_data={"agent_transport": "modern", "agent_version": "0.49.8"})
        mac = "02:00:5E:" + ":".join(suffix[i:i + 2].upper() for i in (0, 2, 4))
        neighbor = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name=f"TEST-DV-NB-{suffix}", status="online", primary_mac=mac)
        db.add_all([device, neighbor])
        db.flush()

        def job(kind, payload, result, minutes, status="success"):
            item = DeviceJob(device_id=device.id, job_type=kind, payload=payload, result=result, status=status,
                             created_at=now - timedelta(minutes=minutes), completed_at=now - timedelta(minutes=minutes))
            db.add(item)
            return item

        old_ping = job("diagnostic_ping", {"target": "192.0.2.1"}, {"target": "192.0.2.1", "data": PING[:2]}, 60)
        other_target = job("diagnostic_ping", {"target": "198.51.100.7"}, {"data": PING[:1]}, 50)
        new_ping = job("diagnostic_ping", {"target": "192.0.2.1"}, {"target": "192.0.2.1", "data": PING}, 5)
        trace_job = job("diagnostic_traceroute", {"target": "192.0.2.1"}, {"data": TRACE}, 4)
        neighbors = job("diagnostic_neighbors", {}, {"data": [{"interface": "ether1", "address": "192.0.2.2", "mac-address": mac.lower(), "identity": "TEST-NB", "platform": "MikroTik", "board": "RB5009", "version": "7.24.4"},
                                                              {"interface": "ether2", "mac-address": "02:00:5E:FF:FF:01", "identity": "TEST-UNKNOWN"}]}, 3)
        dhcp = job("diagnostic_dhcp_lookup", {"query": "192.0.2.50", "lookup_type": "ip"}, {"output": ".id=*5;address=192.0.2.50;mac-address=02:00:5E:AA:BB:01;host-name=TEST-HOST;status=bound;server=dhcp1;dynamic=true", "legacy_transport": True}, 2)
        logs = job("diagnostic_logs", {}, {"output": LEGACY_LOGS, "legacy_transport": True}, 1)
        failed = job("diagnostic_ping", {"target": "203.0.113.1"}, {}, 0, status="failed")
        failed.last_error = "RouterOS ping failed"
        db.commit()
        ids = {k: v.id for k, v in dict(device=device, neighbor=neighbor, old_ping=old_ping, other=other_target, new_ping=new_ping, trace=trace_job,
                                        neighbors=neighbors, dhcp=dhcp, logs=logs, failed=failed).items()}
        assert summary(new_ping) == "25% perdita · media 12.17 ms" and summary(trace_job) == "3 hop · destinazione raggiunta"
        assert summary(logs) == "2 eventi · 1 critical" and summary(failed) == "RouterOS ping failed"

    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-dv-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    base = f"/devices/{ids['device']}/diagnostics"
    page = client.get(base).text
    assert "25% perdita · media 12.17 ms" in page and "3 hop · destinazione raggiunta" in page and "2 eventi · 1 critical" in page

    ping_page = client.get(f"{base}/jobs/{ids['new_ping']}").text
    assert 'data-diagnostic-view="ping"' in ping_page and "3/4" in ping_page and "12.17 ms" in ping_page and "timeout" in ping_page
    assert "data-diagnostic-history" in ping_page and str(ids["old_ping"]) in ping_page and str(ids["other"]) not in ping_page, \
        "history lists earlier runs towards the same target only"
    old_page = client.get(f"{base}/jobs/{ids['old_ping']}").text
    assert "data-diagnostic-history" not in old_page, "later runs are not history of an earlier one"

    trace_page = client.get(f"{base}/jobs/{ids['trace']}").text
    assert 'data-diagnostic-view="traceroute"' in trace_page and "Destinazione raggiunta" in trace_page and "198.51.100.1" in trace_page
    nb_page = client.get(f"{base}/jobs/{ids['neighbors']}").text
    assert f'href="/devices/{ids["neighbor"]}"' in nb_page and "non censito" in nb_page and "RB5009" in nb_page
    dhcp_page = client.get(f"{base}/jobs/{ids['dhcp']}").text
    assert 'data-diagnostic-view="dhcp"' in dhcp_page and "TEST-HOST" in dhcp_page and "dinamica" in dhcp_page and "agent legacy" in dhcp_page
    log_page = client.get(f"{base}/jobs/{ids['logs']}").text
    assert 'data-diagnostic-view="logs"' in log_page and "1 critical" in log_page and "login failure" in log_page
    assert log_page.index("ether2 link down") < log_page.index("login failure"), "newest log entry first"
    failed_page = client.get(f"{base}/jobs/{ids['failed']}").text
    assert "Operazione fallita" in failed_page and "RouterOS ping failed" in failed_page
    print("MikroTik diagnostic views smoke passed")


if __name__ == "__main__":
    main()
