import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { type AclRule, AclRuleEditor } from "../app/AclRuleEditor";
import i18n from "../i18n";

afterEach(cleanup);

const RULES: AclRule[] = [
  { protocol: "tcp", port: 8443, src: "198.51.100.20/32", action: "accept", comment: "tecnico" },
  { protocol: "udp", port: 161, src: "", action: "drop", comment: "snmp" },
];

describe("ACL rule editor", () => {
  it("adds, moves, edits and removes rules, up to the maximum", async () => {
    await i18n.changeLanguage("it");
    const changes: AclRule[][] = [];
    const { rerender } = render(<AclRuleEditor rules={RULES} onChange={(rules) => changes.push(rules)} max={3} />);
    fireEvent.click(screen.getByRole("button", { name: "Aggiungi regola" }));
    expect(changes.pop()).toEqual([...RULES, { protocol: "tcp", port: "", src: "", action: "accept", comment: "" }]);
    fireEvent.click(screen.getAllByRole("button", { name: "Sposta giù" })[0]);
    expect(changes.pop()?.map((rule) => rule.comment)).toEqual(["snmp", "tecnico"]);
    fireEvent.change(screen.getAllByRole("spinbutton")[1], { target: { value: "162" } });
    expect(changes.pop()?.[1].port).toBe("162");
    fireEvent.click(screen.getAllByRole("button", { name: "Togli" })[0]);
    expect(changes.pop()).toEqual([RULES[1]]);
    rerender(<AclRuleEditor rules={[...RULES, RULES[0]]} onChange={(rules) => changes.push(rules)} max={3} />);
    expect(screen.queryByRole("button", { name: "Aggiungi regola" })).toBeNull();
  });

  it("only shows the rules when read-only", async () => {
    await i18n.changeLanguage("it");
    render(<AclRuleEditor rules={RULES} onChange={() => undefined} max={32} readOnly />);
    expect(screen.queryByRole("button")).toBeNull();
    expect((screen.getAllByRole("combobox")[0] as HTMLSelectElement).disabled).toBe(true);
  });
});
