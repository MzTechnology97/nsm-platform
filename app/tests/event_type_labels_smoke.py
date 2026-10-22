"""Every audit event code written by the application has a name in the four interface languages (eventTypes in the locale files)."""
import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$")


def literal_codes(node):
    """Event codes a call argument can hold: a literal, or either branch of `A if cond else B`."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and CODE.match(node.value):
        return {node.value}
    if isinstance(node, ast.IfExp):
        return literal_codes(node.body) | literal_codes(node.orelse)
    return set()


def written_codes():
    codes = set()
    for path in (ROOT / "app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name == "add_event" and len(node.args) > 1:
                codes |= literal_codes(node.args[1])
            if name in ("add_event", "AuditEvent"):
                for keyword in node.keywords:
                    if keyword.arg == "event_type":
                        codes |= literal_codes(keyword.value)
    return codes


def main():
    codes = written_codes()
    assert len(codes) > 150, f"event codes not found ({len(codes)}): the scan no longer matches add_event calls"
    for lang in ("it", "en", "fr", "es"):
        names = json.loads((ROOT / "frontend" / "src" / "i18n" / "locales" / f"{lang}.json").read_text(encoding="utf-8"))["eventTypes"]
        missing = sorted(codes - set(names))
        assert not missing, f"{lang}: event codes without a name in eventTypes: {missing}"
        assert all(isinstance(value, str) and value.strip() for value in names.values()), lang
    print(f"Event type labels smoke passed ({len(codes)} codes)")


if __name__ == "__main__":
    main()
