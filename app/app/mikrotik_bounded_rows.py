"""Bounded RouterOS row collection for the modern (JSON) Agent.

`print as-value` materializes a whole table (a full BGP feed, thousands of
leases) and the serialized result must fit the 64 KiB RouterOS limit of
``/tool fetch http-data``.  Generated handlers therefore:

1. read rows by id with ``get`` up to a per-menu limit, counting the rest;
2. serialize, and while the JSON body is above ``MAX_JSON_BODY`` halve the
   number of rows sent per list, flagging the result as truncated.
"""
from __future__ import annotations

import textwrap

MAX_JSON_BODY = 60000
ROW_LIMITS = {
    "/ip address": 500,
    "/ip route": 300,
    "/interface": 300,
    "/ip firewall filter": 300,
    "/ip firewall nat": 300,
    "/ip dhcp-server lease": 500,
    "/ppp active": 500,
}
TUNNEL_CLIENT_LIMIT = 100


def collect(menu: str, var: str, *, limit: int | None = None, optional: bool = False) -> str:
    """RouterOS code filling list ``$var`` (declared by the caller) from ``menu``."""
    limit = limit or ROW_LIMITS.get(menu, 300)
    body = (
        f":local nsmIds [{menu} find]\n"
        ":set nsmTotal ($nsmTotal + [:len $nsmIds])\n"
        f":if ([:len $nsmIds] > {limit}) do={{ :set nsmIds [:pick $nsmIds 0 {limit}]; :set nsmTruncated true }}\n"
        ":local nsmIdx 0\n"
        f":foreach nsmId in=$nsmIds do={{ :set (${var}->$nsmIdx) [{menu} get $nsmId]; :set nsmIdx ($nsmIdx + 1) }}\n"
    )
    return ":do {\n" + textwrap.indent(body, "  ") + ("} on-error={}\n" if optional else "}\n")


def fit_loop(lists: list[str], shape: str, serialize: str, body_var: str) -> str:
    """Loop shaping ``$nsmData`` with ``$nsmCap`` rows per list until ``body_var`` fits.

    ``shape`` assigns nsmData using ``[:pick $list 0 $nsmCap]``; ``serialize``
    is the expression producing the JSON body from nsmData.
    """
    lengths = ";".join(f"[:len ${name}]" for name in lists)
    return (
        ":local nsmMax 0\n"
        f":foreach nsmLen in={{{lengths}}} do={{ :if ($nsmLen > $nsmMax) do={{ :set nsmMax $nsmLen }} }}\n"
        ":local nsmCap $nsmMax\n"
        f":local {body_var} \"\"\n"
        ":local nsmFitting true\n"
        ":while ($nsmFitting) do={\n"
        + textwrap.indent(shape, "  ")
        + f"  :set {body_var} {serialize}\n"
        f"  :if (([:len ${body_var}] <= {MAX_JSON_BODY}) || ($nsmCap = 0)) do={{ :set nsmFitting false }} else={{ :set nsmCap ($nsmCap / 2); :set nsmTruncated true }}\n"
        "}\n"
    )
