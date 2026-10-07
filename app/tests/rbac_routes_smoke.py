"""Route-level RBAC guard: every portal route needs a login, the read-only auditor cannot write.

The test walks every registered route, so a new page or action is covered
automatically.  Anonymous requests must end on the login page or be refused.
POST/PUT/PATCH/DELETE requests by the auditor must be refused (401/403 or a
"not authorized" flash), rejected as invalid before acting (400/404/405/409/
422) or appear in the short, explained allow-list below.
"""
import re
import uuid

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, BackupPolicy, Customer, Device, Site, User
from app.security import hash_password

PASSWORD = "CI-RBAC-Routes-2026"
PUBLIC_PREFIXES = ("/login", "/logout", "/static", "/healthz", "/health", "/api/v1/agents/", "/api/v1/enrollment/",
                   "/branding/", "/api/v1/public/", "/favicon")
PUBLIC_EXACT = {"/", "/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"}
# Writes a read-only auditor may legitimately perform.
AUDITOR_ALLOWED = {
    ("POST", "/notifications/{notification_id}/read"): "marks the auditor's own notification as read",
    ("POST", "/notifications/read-all"): "marks the auditor's own notifications as read",
    ("POST", "/devices/{device_id}/firmware-readiness"): "read-only update check (firmware.read)",
}
REFUSED_FLASH = re.compile(r"Operazione non autorizzata|Permesso insufficiente|non hai i permessi", re.I)
SUCCESS_FLASH = re.compile(r'data-flash-message[^>]*>\s*<[^>]*>\s*✓', re.S)


def csrf_from(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def main():
    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        customer = Customer(name=f"CI RBAC {suffix}", code=f"RB{suffix}")
        db.add(customer)
        db.flush()
        site = Site(customer_id=customer.id, name="CI RBAC site")
        device = Device(customer_id=customer.id, vendor="mikrotik", device_type="router", name="CI-RBAC-DEV", status="online", firmware_version="7.24.4")
        db.add_all([site, device])
        db.flush()
        issue = ActionIssue(category="backup", severity="warning", status="open", title="CI RBAC issue", details={}, customer_id=customer.id, device_id=device.id)
        policy = BackupPolicy(name=f"CI RBAC policy {suffix}", is_enabled=True, scope_type="device", device_id=device.id)
        auditor = User(username=f"ci-rbac-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True)
        db.add_all([issue, policy, auditor])
        db.commit()
        ids = {"customer_id": customer.id, "site_id": site.id, "device_id": device.id, "issue_id": issue.id, "policy_id": policy.id, "user_id": auditor.id}

    def concrete(path):
        return re.sub(r"\{([^}]+)\}", lambda m: str(ids.get(m.group(1), uuid.uuid4())), path)

    anonymous = TestClient(app)
    client = TestClient(app)
    assert client.post("/login", data={"username": f"ci-rbac-{suffix}", "password": PASSWORD, "csrf": csrf_from(client.get("/login").text)}, follow_redirects=False).status_code == 303
    token = csrf_from(client.get("/devices").text)

    open_routes, auditor_writes, checked = [], [], 0
    for route in app.router.routes:
        if not isinstance(route, APIRoute) or route.path in PUBLIC_EXACT or route.path.startswith(PUBLIC_PREFIXES):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            checked += 1
            url = concrete(route.path)
            response = anonymous.get(url) if method == "GET" else anonymous.request(method, url, data={"csrf": "x"})
            if response.status_code == 200 and "/login" not in str(response.url):
                open_routes.append(f"{method} {route.path}")
            if method == "GET" or (method, route.path) in AUDITOR_ALLOWED:
                continue
            response = client.request(method, url, data={"csrf": token}, follow_redirects=False)
            if response.status_code in (400, 401, 403, 404, 405, 409, 422):
                continue
            if response.status_code in (302, 303) and response.headers.get("location"):
                page = client.get(response.headers["location"]).text
                if REFUSED_FLASH.search(page) or not SUCCESS_FLASH.search(page):
                    continue
            auditor_writes.append(f"{method} {route.path} -> {response.status_code}")
    assert checked > 80, checked
    assert not open_routes, f"routes reachable without login: {open_routes}"
    assert not auditor_writes, f"writes accepted for the read-only auditor: {auditor_writes}"
    print(f"RBAC route guard smoke passed ({checked} route/method pairs)")


if __name__ == "__main__":
    main()
