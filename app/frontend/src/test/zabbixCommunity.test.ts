import { describe, expect, it } from "vitest";
import { isZabbixMacro } from "../api/admin";

describe("isZabbixMacro", () => {
  it("accepts Zabbix user macros, with or without context", () => {
    expect(isZabbixMacro("{$SNMP_COMMUNITY}")).toBe(true);
    expect(isZabbixMacro(" {$SNMP_COMMUNITY} ")).toBe(true);
    expect(isZabbixMacro('{$SNMP_COMMUNITY:"core"}')).toBe(true);
  });

  it("treats anything else as a literal community (a secret)", () => {
    expect(isZabbixMacro("example-community")).toBe(false);
    expect(isZabbixMacro("{$}")).toBe(false);
    expect(isZabbixMacro("{$A B}")).toBe(false);
    expect(isZabbixMacro("x{$SNMP_COMMUNITY}")).toBe(false);
    expect(isZabbixMacro("")).toBe(false);
  });
});
