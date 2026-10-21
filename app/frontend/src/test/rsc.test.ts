import { describe, expect, it } from "vitest";

import { rscSections, tokenizeRsc } from "../lib/rsc";

const kinds = (line: string) => tokenizeRsc(line).filter((token) => token.text.trim()).map((token) => `${token.kind}:${token.text}`);

describe("RouterOS export highlighting", () => {
  it("splits paths, commands, keys, values and strings", () => {
    expect(kinds("/ip firewall filter")).toEqual(["path:/ip firewall filter"]);
    expect(kinds('add action=accept chain=input comment="allow a=b" disabled=yes')).toEqual([
      "command:add", "key:action", "text:=", "value:accept", "key:chain", "text:=", "value:input",
      "key:comment", "text:=", 'string:"allow a=b"', "key:disabled", "text:=", "value:yes",
    ]);
    expect(kinds("/ip address add address=192.0.2.1/24 interface=ether1")).toEqual([
      "path:/ip address", "command:add", "key:address", "text:=", "value:192.0.2.1/24", "key:interface", "text:=", "value:ether1",
    ]);
    expect(kinds("# 2026-10-21 by RouterOS 7.19.4")).toEqual(["comment:# 2026-10-21 by RouterOS 7.19.4"]);
  });

  it("keeps every character of the line", () => {
    for (const line of ['    set [ find default-name=ether1 ] comment="WAN \\"main\\""', "/system identity", "  \\", 'add name="unterminated']) {
      expect(tokenizeRsc(line).map((token) => token.text).join("")).toBe(line);
    }
  });

  it("lists the sections with their first line and size", () => {
    const lines = ["# header", "/interface bridge", "add name=bridge", "/ip address", "add address=192.0.2.1/24 interface=bridge", "add address=198.51.100.1/24 interface=ether2"];
    expect(rscSections(lines)).toEqual([
      { path: "/interface bridge", line: 2, size: 2 },
      { path: "/ip address", line: 4, size: 3 },
    ]);
  });
});
