"""MITRE ATT&CK and regulatory mapping of the Action Center issue categories (SIEM-05, step 1).

Every issue category NSM raises carries the ATT&CK techniques it can reveal (with their tactics) and the
requirements whose controls it evidences: NIS2 (art. 21(2) measures, in Italy D.Lgs. 138/2024 art. 24),
ISO/IEC 27001:2022 Annex A and GDPR art. 32. The Action Center shows them and filters by tactic and framework.
The mapping supports the operator's own assessment; it is not a certification. Categories of the platform itself
(system, integration, reports, other) carry nothing.
"""
from __future__ import annotations

TACTICS = {
    "TA0001": "Initial Access",
    "TA0003": "Persistence",
    "TA0005": "Defense Evasion",
    "TA0006": "Credential Access",
    "TA0040": "Impact",
}
# id: (name, tactics)
TECHNIQUES = {
    "T1078": ("Valid Accounts", ("TA0001", "TA0003", "TA0005")),
    "T1110": ("Brute Force", ("TA0006",)),
    "T1133": ("External Remote Services", ("TA0001", "TA0003")),
    "T1190": ("Exploit Public-Facing Application", ("TA0001",)),
    "T1498": ("Network Denial of Service", ("TA0040",)),
    "T1529": ("System Shutdown/Reboot", ("TA0040",)),
    "T1562": ("Impair Defenses", ("TA0005",)),
    "T1601": ("Modify System Image", ("TA0005",)),
}
FRAMEWORKS = ("nis2", "iso27001", "gdpr")
# id: (framework, code shown); the titles are translated in the interface (attack.requirements.<id>).
REQUIREMENTS = {
    "nis2_21_2_b": ("nis2", "art. 21(2)(b)"),   # incident handling
    "nis2_21_2_c": ("nis2", "art. 21(2)(c)"),   # business continuity, backup, disaster recovery
    "nis2_21_2_e": ("nis2", "art. 21(2)(e)"),   # security in acquisition and maintenance, vulnerability handling
    "nis2_21_2_f": ("nis2", "art. 21(2)(f)"),   # assessing the effectiveness of the measures
    "nis2_21_2_i": ("nis2", "art. 21(2)(i)"),   # access control and asset management
    "iso_a_5_7": ("iso27001", "A.5.7"),         # threat intelligence
    "iso_a_8_5": ("iso27001", "A.8.5"),         # secure authentication
    "iso_a_8_8": ("iso27001", "A.8.8"),         # management of technical vulnerabilities
    "iso_a_8_9": ("iso27001", "A.8.9"),         # configuration management
    "iso_a_8_13": ("iso27001", "A.8.13"),       # information backup
    "iso_a_8_15": ("iso27001", "A.8.15"),       # logging
    "iso_a_8_16": ("iso27001", "A.8.16"),       # monitoring activities
    "iso_a_8_20": ("iso27001", "A.8.20"),       # networks security
    "iso_a_8_32": ("iso27001", "A.8.32"),       # change management
    "gdpr_32_1_b": ("gdpr", "art. 32(1)(b)"),   # confidentiality, integrity, availability and resilience
    "gdpr_32_1_c": ("gdpr", "art. 32(1)(c)"),   # restoring availability after an incident
    "gdpr_32_1_d": ("gdpr", "art. 32(1)(d)"),   # regularly testing the effectiveness of the measures
}
_AVAILABILITY = {"techniques": (), "requirements": ("nis2_21_2_c", "iso_a_8_16", "gdpr_32_1_b")}
_OWN_ADDRESSES = {"techniques": (), "requirements": ("nis2_21_2_b", "iso_a_5_7", "iso_a_8_20")}
CATEGORIES = {
    "security_access": {"techniques": ("T1110", "T1078"), "requirements": ("nis2_21_2_b", "nis2_21_2_i", "iso_a_8_5", "iso_a_8_16", "gdpr_32_1_b")},
    "threat_list": {"techniques": ("T1078", "T1110"), "requirements": ("nis2_21_2_b", "iso_a_5_7", "iso_a_8_16")},
    "syslog_rule": {"techniques": (), "requirements": ("nis2_21_2_b", "iso_a_8_15", "iso_a_8_16")},
    "exposure": {"techniques": ("T1133", "T1190"), "requirements": ("nis2_21_2_e", "iso_a_8_20", "gdpr_32_1_b")},
    "security": {"techniques": ("T1190",), "requirements": ("nis2_21_2_e", "iso_a_8_8", "gdpr_32_1_b")},
    "configuration": {"techniques": ("T1562",), "requirements": ("nis2_21_2_e", "iso_a_8_9", "iso_a_8_32")},
    "compliance": {"techniques": (), "requirements": ("nis2_21_2_f", "iso_a_8_9", "gdpr_32_1_d")},
    "lifecycle": {"techniques": (), "requirements": ("nis2_21_2_e", "iso_a_8_8")},
    "firmware": {"techniques": ("T1601",), "requirements": ("nis2_21_2_e", "iso_a_8_8")},
    "backup": {"techniques": (), "requirements": ("nis2_21_2_c", "iso_a_8_13", "gdpr_32_1_c")},
    "dnsbl": _OWN_ADDRESSES,
    "abuseipdb": _OWN_ADDRESSES,
    "ripestat": _OWN_ADDRESSES,
    "reachability": {"techniques": ("T1498", "T1529"), "requirements": _AVAILABILITY["requirements"]},
    "latency": _AVAILABILITY,
    "connectivity": _AVAILABILITY,
    "monitoring": _AVAILABILITY,
    "interface_errors": _AVAILABILITY,
    "acs": _AVAILABILITY,
    "agent": {"techniques": (), "requirements": ("iso_a_8_16",)},
}


def describe(category: str | None) -> dict:
    """Techniques (with names and tactics) and requirements of one issue category; empty when unmapped."""
    entry = CATEGORIES.get(category or "", {"techniques": (), "requirements": ()})
    return {
        "techniques": [{"id": tid, "name": TECHNIQUES[tid][0], "tactics": list(TECHNIQUES[tid][1])} for tid in entry["techniques"]],
        "requirements": [{"id": rid, "framework": REQUIREMENTS[rid][0], "code": REQUIREMENTS[rid][1]} for rid in entry["requirements"]],
    }


def categories_for(tactic: str = "", framework: str = "") -> set[str]:
    """Issue categories that reveal a technique of ``tactic`` and/or evidence a requirement of ``framework``."""
    out = set()
    for category, entry in CATEGORIES.items():
        if tactic and not any(tactic in TECHNIQUES[tid][1] for tid in entry["techniques"]):
            continue
        if framework and not any(REQUIREMENTS[rid][0] == framework for rid in entry["requirements"]):
            continue
        out.add(category)
    return out


def options() -> dict:
    return {"tactics": [{"id": key, "name": name} for key, name in TACTICS.items()], "frameworks": list(FRAMEWORKS)}
