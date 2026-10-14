import { describe, expect, it } from "vitest";

import { type LogEntry, logsUrl, mergeNewer } from "../api/syslog";

const entry = (id: number): LogEntry => ({
  id, received_at: "2026-10-14T10:00:00+00:00", severity: 6, severity_label: "info", topics: "system", hostname: "", source_ip: "198.51.100.1", message: `line ${id}`, category: null,
});

describe("logsUrl", () => {
  it("builds the tail and history queries", () => {
    expect(logsUrl("d1", {})).toBe("/api/v1/devices/d1/logs?limit=200");
    expect(logsUrl("d1", { severity: "4", q: "pppoe out", after: 42, limit: 500 })).toBe("/api/v1/devices/d1/logs?severity=4&q=pppoe+out&after_id=42&limit=500");
    expect(logsUrl("d1", { before: 7 })).toBe("/api/v1/devices/d1/logs?before_id=7&limit=200");
  });
});

describe("mergeNewer", () => {
  it("puts new lines on top and ignores lines already shown", () => {
    const merged = mergeNewer([entry(3), entry(2)], [entry(5), entry(4), entry(3)]);
    expect(merged.map((e) => e.id)).toEqual([5, 4, 3, 2]);
  });

  it("keeps at most 2000 lines", () => {
    const current = Array.from({ length: 2000 }, (_, i) => entry(2000 - i));
    const merged = mergeNewer(current, [entry(2001)]);
    expect(merged).toHaveLength(2000);
    expect(merged[0].id).toBe(2001);
    expect(merged[1999].id).toBe(2);
  });
});
