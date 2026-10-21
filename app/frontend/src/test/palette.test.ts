import { beforeEach, describe, expect, it } from "vitest";

import { type Entry, fold, readRecent, remember } from "../app/CommandPalette";

const device = (id: string): Entry => ({ key: `device:${id}`, kind: "device", label: `Router ${id}`, target: { kind: "device", id } });

describe("command palette", () => {
  beforeEach(() => localStorage.clear());

  it("matches without accents or case", () => {
    expect(fold("Vulnerabilità")).toBe("vulnerabilita");
    expect(fold("Sécurité").includes(fold("SECURITE"))).toBe(true);
  });

  it("remembers opened devices once, newest first, at most six", () => {
    for (const id of ["a", "b", "c", "a"]) remember(device(id));
    expect(readRecent().map((entry) => entry.key)).toEqual(["device:a", "device:c", "device:b"]);
    for (const id of ["d", "e", "f", "g", "h"]) remember(device(id));
    expect(readRecent()).toHaveLength(6);
  });

  it("does not remember pages or the search shortcut", () => {
    remember({ key: "page:devices", kind: "page", label: "Apparati", target: { kind: "page", route: "/devices" } });
    remember({ key: "all", kind: "all", label: "Tutti", target: { kind: "all", q: "x" } });
    expect(readRecent()).toEqual([]);
  });
});
