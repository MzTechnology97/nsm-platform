"""PON optical levels of TR-069 CPEs (ACS-05 follow-up), read by the monitoring from GenieACS.

Where the values are:

- TR-181: ``Device.Optical.Interface.{i}`` (``OpticalSignalLevel`` and ``TransmitOpticalLevel`` in 0.001 dBm,
  ``Status``); temperature, voltage and bias are vendor parameters of the same object (``X_<OUI>_Temperature``…);
- TR-098: vendor objects under ``InternetGatewayDevice.WANDevice.{i}`` such as ``X_GponInterafceConfig``
  (Huawei, spelled like that), ``X_ZTE-COM_WANPONInterfaceConfig``, ``X_CT-COM_GponInterfaceConfig`` /
  ``X_CT-COM_EponInterfaceConfig``, ``X_CMCC_GponInterfaceConfig``, ``WANPONInterfaceConfig`` (any object of a
  WAN device with ``PON``/``Optic`` in its name is read).

Vendors do not agree on units, so each value is normalized and kept only when it is plausible:

- power: dBm as reported (negative or decimal values), 0.001 dBm (TR-181 integers), or 0.1 µW (CT-COM style
  positive integers) converted to dBm;
- temperature: °C, or 1/256 °C (SFF-8472 style);
- voltage: V, mV or 100 µV;
- bias: mA, µA, or 2 µA for CT-COM style objects.

A value that fits none of these is left empty rather than guessed. The NSM base provision re-reads these
objects every hour (never a refresh of the root); nothing is ever written to the CPE.
"""
from __future__ import annotations

import math
import re

PROJECTION = ("Device.Optical",)
# Read once an hour by the NSM base provision (acs_admin): values only, the paths GenieACS already knows.
PROVISION_PATHS = (
    "Device.Optical.Interface.*.*",
    "InternetGatewayDevice.WANDevice.*.X_GponInterafceConfig.*",
    "InternetGatewayDevice.WANDevice.*.X_GponInterfaceConfig.*",
    "InternetGatewayDevice.WANDevice.*.X_ZTE-COM_WANPONInterfaceConfig.*",
    "InternetGatewayDevice.WANDevice.*.X_CT-COM_GponInterfaceConfig.*",
    "InternetGatewayDevice.WANDevice.*.X_CT-COM_EponInterfaceConfig.*",
    "InternetGatewayDevice.WANDevice.*.X_CMCC_GponInterfaceConfig.*",
    "InternetGatewayDevice.WANDevice.*.WANPONInterfaceConfig.*",
)
FIELDS = ("rx_dbm", "tx_dbm", "temperature_c", "voltage_v", "bias_ma")
_OBJECT = re.compile(r"optic|[gex]pon|(?:^|_|wan)pon", re.I)
_VENDOR = re.compile(r"^X_[A-Za-z0-9-]+?_")
_CT_STYLE = re.compile(r"CT-COM|CMCC|CU_|X_CU", re.I)
NAMES = {
    "rx": {"rxpower", "rxopticalpower", "opticalsignallevel", "receivepower", "rxpwr", "receiveopticalpower", "rxopticallevel",
           "opticalrxpower", "receivedopticalpower"},
    "tx": {"txpower", "txopticalpower", "transmitopticallevel", "transmitpower", "txpwr", "transmitopticalpower", "opticaltxpower"},
    "temperature": {"transceivertemperature", "temperature", "temp", "opticaltemperature", "moduletemperature"},
    "voltage": {"supplyvoltage", "supplyvottage", "voltage", "vottage", "supplyvolt", "transceivervoltage"},
    "bias": {"biascurrent", "bias", "txbias", "laserbiascurrent", "txbiascurrent"},
    "status": {"status", "linkstatus", "ponstatus", "onustatus"},
}


def _key(name: str) -> str:
    return re.sub(r"[^a-z]", "", _VENDOR.sub("", name).lower())


def _leaf(node, name):
    leaf = node.get(name) if isinstance(node, dict) else None
    if isinstance(leaf, dict):
        return leaf.get("_value"), leaf.get("_timestamp")
    return None, None


def _number(value) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _decimal(value) -> bool:
    return "." in str(value) or "e" in str(value).lower()


def power_dbm(value, standard: bool = False) -> float | None:
    """Optical power in dBm; ``standard`` is a TR-181 level (0.001 dBm)."""
    number = _number(value)
    if number is None:
        return None
    if standard and abs(number) > 100:
        number /= 1000
    elif number > 15 and not _decimal(value):
        number = 10 * math.log10(number / 10000)  # 0.1 µW → dBm
    elif abs(number) > 100:
        number /= 1000
    return round(number, 2) if -50 <= number <= 15 else None


def temperature_c(value) -> float | None:
    number = _number(value)
    if number is None:
        return None
    for divisor in (1, 256):
        if -40 <= number / divisor <= 110:
            return round(number / divisor, 1)
    return None


def voltage_v(value) -> float | None:
    number = _number(value)
    if number is None:
        return None
    for divisor in (1, 1000, 10000):
        if 1 <= number / divisor <= 6:
            return round(number / divisor, 2)
    return None


def bias_ma(value, ct_style: bool = False) -> float | None:
    number = _number(value)
    if number is None or number < 0:
        return None
    if ct_style and not _decimal(value):
        number *= 0.002
    elif number > 200:
        number /= 1000
    return round(number, 2) if number <= 200 else None


def _read(node: dict, name: str, standard: bool) -> dict | None:
    """Optical values of one object; None when it has none."""
    ct_style = bool(_CT_STYLE.search(name))
    found: dict = {}
    stamps = []
    for child in node:
        if child.startswith("_"):
            continue
        key = _key(child)
        value, stamp = _leaf(node, child)
        if value is None or value == "":
            continue
        for field, names in NAMES.items():
            if key in names and field not in found:
                found[field] = value
                if field != "status" and stamp:
                    stamps.append(str(stamp))
    values = {
        "rx_dbm": power_dbm(found.get("rx"), standard),
        "tx_dbm": power_dbm(found.get("tx"), standard),
        "temperature_c": temperature_c(found.get("temperature")),
        "voltage_v": voltage_v(found.get("voltage")),
        "bias_ma": bias_ma(found.get("bias"), ct_style),
    }
    if all(values[field] is None for field in FIELDS):
        return None
    return {**values, "status": str(found["status"]) if found.get("status") is not None else None, "source": name,
            "read_at": max(stamps) if stamps else None}


def _instances(node) -> list[tuple[str, dict]]:
    if not isinstance(node, dict):
        return []
    return [(key, node[key]) for key in sorted((k for k in node if k.isdigit() and isinstance(node[k], dict)), key=int)]


def parse(row: dict) -> dict | None:
    """Optical levels of the first PON interface that reports any, or None when the CPE exposes none."""
    device = row.get("Device")
    if isinstance(device, dict):
        optical = (device.get("Optical") or {}).get("Interface") if isinstance(device.get("Optical"), dict) else None
        for index, interface in _instances(optical):
            result = _read(interface, f"Device.Optical.Interface.{index}", standard=True)
            if result:
                return result
        return None
    root = row.get("InternetGatewayDevice")
    if not isinstance(root, dict):
        return None
    for index, wan in _instances(root.get("WANDevice")):
        for name, node in wan.items():
            if isinstance(node, dict) and not name.startswith("_") and _OBJECT.search(name):
                result = _read(node, f"InternetGatewayDevice.WANDevice.{index}.{name}", standard=False)
                if result:
                    return result
    return None
