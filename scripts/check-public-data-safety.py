#!/usr/bin/env python3
"""Fail CI when public source or fixtures expose deployment-derived data."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
ALLOW_MARKER = "public-data-safety: allow"

FIXTURE_PATHS = [
    ROOT / "app" / "tests",
    ROOT / "docs",
    ROOT / "app" / "app" / "demo.py",
    ROOT / "README.md",
    ROOT / "README-FIRST.md",
    ROOT / "scripts",
]

TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".sh", ".yml", ".yaml", ".json", ".csv",
    ".toml", ".ini", ".env", ".example", ".html", ".js", ".css",
}

DOC_IPV4 = tuple(
    ipaddress.ip_network(value)
    for value in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)
RFC1918 = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)

IPV4_RE = re.compile(r"(?<![0-9.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9.])")
MAC_RE = re.compile(r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}(?![0-9a-f])")
HOME_RE = re.compile(r"/home/([A-Za-z0-9._-]+)/")

SECRET_PATTERNS = (
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("GitHub classic token", re.compile(r"\bghp_[A-Za-z0-9]{20,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
)

# Regression deny-list for deployment-derived identifiers observed during the
# public-repository audit. Keeping these here prevents accidental reintroduction.
KNOWN_DEPLOYMENT_MARKERS = (
    "172.31.0.28",
    "W-AP-R-CDA_NET",
    "D4:01:C3:51:80:8F",
    "40:AE:30:2F:6E:35",
    "10.29.28.2/30",
    "172.31.1.0/24",
    "/home/cda/",
)


def text_candidate(path: Path) -> bool:
    if not path.is_file() or path.resolve() == SELF:
        return False
    if ".git" in path.parts:
        return False
    if path.name in {"Dockerfile", "Makefile"}:
        return True
    return path.suffix.lower() in TEXT_SUFFIXES


def iter_repo_text_files():
    for item in sorted(ROOT.rglob("*")):
        if text_candidate(item):
            yield item


def iter_fixture_files():
    seen: set[Path] = set()
    for path in FIXTURE_PATHS:
        if not path.exists():
            continue
        candidates = [path] if path.is_file() else sorted(path.rglob("*"))
        for item in candidates:
            if not text_candidate(item):
                continue
            resolved = item.resolve()
            if resolved not in seen:
                seen.add(resolved)
                yield item


def is_doc_ipv4(address: ipaddress.IPv4Address) -> bool:
    return any(address in network for network in DOC_IPV4)


def is_rfc1918(address: ipaddress.IPv4Address) -> bool:
    return any(address in network for network in RFC1918)


def is_locally_administered(mac: str) -> bool:
    first = int(mac.replace("-", ":").split(":", 1)[0], 16)
    return bool(first & 0x02)


def read_lines(path: Path):
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return []


def main() -> int:
    findings: list[str] = []
    repo_scanned = 0
    fixture_scanned = 0

    # Global repository pass: only high-signal secret signatures and identifiers
    # previously confirmed as deployment-derived. Application code is allowed to
    # understand arbitrary customer/private networks, so RFC1918/MAC policy is
    # intentionally not applied globally.
    for path in iter_repo_text_files():
        repo_scanned += 1
        rel = path.relative_to(ROOT)
        for number, line in enumerate(read_lines(path), 1):
            if ALLOW_MARKER in line:
                continue
            for marker in KNOWN_DEPLOYMENT_MARKERS:
                if marker.lower() in line.lower():
                    findings.append(f"{rel}:{number}: known deployment marker: {marker}")
            for label, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(f"{rel}:{number}: possible {label}")

    # Fixture/documentation pass: enforce public-safe deterministic examples.
    for path in iter_fixture_files():
        fixture_scanned += 1
        rel = path.relative_to(ROOT)
        for number, line in enumerate(read_lines(path), 1):
            if ALLOW_MARKER in line:
                continue

            for raw in IPV4_RE.findall(line):
                try:
                    address = ipaddress.ip_address(raw)
                except ValueError:
                    continue
                if isinstance(address, ipaddress.IPv4Address) and is_rfc1918(address) and not is_doc_ipv4(address):
                    findings.append(
                        f"{rel}:{number}: RFC1918 fixture {raw}; use RFC 5737 TEST-NET unless explicitly required"
                    )

            for mac in MAC_RE.findall(line):
                normalized = mac.replace("-", ":").upper()
                if normalized == "FF:FF:FF:FF:FF:FF":
                    continue
                first = int(normalized.split(":", 1)[0], 16)
                if first & 0x01:  # multicast/group address, not a hardware fixture
                    continue
                if not is_locally_administered(normalized):
                    findings.append(
                        f"{rel}:{number}: globally administered MAC fixture {normalized}; use a 02: locally administered address"
                    )

            for match in HOME_RE.finditer(line):
                user = match.group(1)
                if user not in {"example", "user", "USERNAME"}:
                    findings.append(
                        f"{rel}:{number}: deployment-like home path /home/{user}/; derive it at runtime or use a generic placeholder"
                    )

    unique = sorted(set(findings))
    if unique:
        print("Public data-safety guard failed:\n")
        for finding in unique:
            print(f"- {finding}")
        print(
            f"\n{len(unique)} finding(s); scanned {repo_scanned} repository text files "
            f"and {fixture_scanned} fixture/documentation files."
        )
        print(f"If a literal is genuinely required, document why and add `{ALLOW_MARKER}` on that line.")
        return 1

    print(
        f"Public data-safety guard passed: {repo_scanned} repository text files and "
        f"{fixture_scanned} fixture/documentation files scanned."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
