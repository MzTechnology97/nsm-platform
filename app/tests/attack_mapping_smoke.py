"""SIEM-05: ATT&CK and regulatory mapping of the issue categories, shown and filtered in the Action Center."""
import pathlib
import re
import uuid

from fastapi.testclient import TestClient

from app import attack_mapping as mapping
from app.db import SessionLocal
from app.entrypoint import app
from app.models import ActionIssue, Customer, User
from app.security import hash_password

PASSWORD = "CI-Attack-Mapping-2026"
PLATFORM = {"system", "integration", "reports", "other"}  # categories of NSM itself, deliberately unmapped


def main():
    # The catalog is consistent: every technique, tactic and requirement referenced exists.
    for category, entry in mapping.CATEGORIES.items():
        assert entry["requirements"], category
        for tid in entry["techniques"]:
            assert tid in mapping.TECHNIQUES, (category, tid)
        for rid in entry["requirements"]:
            assert rid in mapping.REQUIREMENTS, (category, rid)
    for name, tactics in mapping.TECHNIQUES.values():
        assert set(tactics) <= set(mapping.TACTICS), name
    assert {framework for framework, _ in mapping.REQUIREMENTS.values()} == set(mapping.FRAMEWORKS)

    # Every category the code raises is mapped (or is a platform category), so a new one cannot be forgotten.
    source = pathlib.Path(__file__).resolve().parents[1] / "app"
    raised = set()
    for path in source.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        raised |= set(re.findall(r'^[A-Z_]*CATEGORY = "([a-z_]+)"', text, re.M))
        raised |= set(re.findall(r'\bcategory="([a-z_]+)"', text))
    missing = sorted(raised - set(mapping.CATEGORIES) - PLATFORM)
    assert not missing, f"issue categories without ATT&CK/framework mapping: {missing}"

    assert mapping.categories_for(tactic="TA0006") == {"security_access", "threat_list"}
    assert "backup" in mapping.categories_for(framework="gdpr") and "backup" not in mapping.categories_for(tactic="TA0001")
    assert mapping.describe("system") == {"techniques": [], "requirements": []}

    suffix = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        customer = Customer(name=f"CI Attack Mapping {suffix}", code=f"AM{suffix}")
        db.add(customer); db.flush()
        for title, category in (("access", "security_access"), ("backup", "backup"), ("platform", "system")):
            db.add(ActionIssue(title=f"TEST {title} {suffix}", severity="high", category=category, status="open", details={}, customer_id=customer.id))
        db.add(User(username=f"ci-am-{suffix}", password_hash=hash_password(PASSWORD), role="auditor", is_active=True))
        db.commit()
        customer_id = str(customer.id)

    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    assert client.post("/login", data={"username": f"ci-am-{suffix}", "password": PASSWORD, "csrf": token}, follow_redirects=False).status_code == 303

    def titles(**params):
        body = client.get("/api/ui/v1/action-center", params={"customer": customer_id, **params}).json()
        return body, {row["title"].split()[1]: row for row in body["items"]}

    body, rows = titles()
    assert set(rows) == {"access", "backup", "platform"}
    assert [t["id"] for t in rows["access"]["techniques"]] == ["T1110", "T1078"]
    assert {r["framework"] for r in rows["access"]["requirements"]} == {"nis2", "iso27001", "gdpr"}
    assert rows["platform"]["techniques"] == [] and rows["platform"]["requirements"] == []
    assert body["mapping"]["frameworks"] == ["nis2", "iso27001", "gdpr"] and {"id": "TA0006", "name": "Credential Access"} in body["mapping"]["tactics"]

    body, rows = titles(tactic="TA0006")
    assert set(rows) == {"access"} and body["scope_total"] == 1, body
    _, rows = titles(framework="gdpr")
    assert set(rows) == {"access", "backup"}
    _, rows = titles(tactic="TA0040", framework="gdpr")
    assert not rows
    print("SIEM-05 attack mapping smoke passed")


if __name__ == "__main__":
    main()
