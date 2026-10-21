import { describe, expect, it } from "vitest";

import { devicesUrl } from "../api/devices";
import { parseDeviceSearch } from "../app/router";
import { formatRelative } from "../lib/format";

describe("device list", () => {
  it("keeps only known string filters from the address bar", () => {
    expect(parseDeviceSearch({ q: "ccr", status: "online", dir: "sideways", page: 3, evil: "x", vendor: "" })).toEqual({ q: "ccr", status: "online" });
    expect(parseDeviceSearch({ sort: "last_seen", dir: "desc" })).toEqual({ sort: "last_seen", dir: "desc" });
  });

  it("builds the API URL with filters and the block to load", () => {
    const url = new URL(devicesUrl({ q: "router 1", vendor: "mikrotik", status: "" }, 200), "http://nsm.example.test");
    expect(url.pathname).toBe("/api/ui/v1/devices");
    expect(Object.fromEntries(url.searchParams)).toEqual({ q: "router 1", vendor: "mikrotik", offset: "200", limit: "100" });
  });

  it("formats the last contact in the user's language", () => {
    const now = Date.parse("2026-10-14T12:00:00Z");
    expect(formatRelative("2026-10-14T11:55:00Z", "it", now)).toMatch(/5 min/);
    expect(formatRelative("2026-10-14T09:00:00Z", "en", now)).toMatch(/3 hr/);
    expect(formatRelative("2026-10-12T12:00:00Z", "fr", now)).toMatch(/2 j|avant-hier/);
    expect(formatRelative("2026-09-01T12:00:00Z", "es", now)).toMatch(/2026/);
    expect(formatRelative(null, "it", now)).toBe("—");
  });
});

describe("uptime", () => {
  it("shows days and hours, hours and minutes, or minutes", async () => {
    const { formatDuration } = await import("../lib/format");
    expect(formatDuration(788645, "en")).toBe("9d 3h");
    expect(formatDuration(7200 + 60 * 5, "en")).toBe("2h 5m");
    expect(formatDuration(59, "en")).toBe("0m");
    expect(formatDuration(null, "en")).toBe("—");
  });
});
