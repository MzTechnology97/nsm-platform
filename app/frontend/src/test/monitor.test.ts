import { describe, expect, it } from "vitest";

import { columns } from "../api/monitor";
import { formatTick, formatValue, seriesStats } from "../charts/format";

describe("formatValue", () => {
  it("scales bit rates and byte volumes", () => {
    expect(formatValue(950, "bps", "en")).toBe("950 bit/s");
    expect(formatValue(12_500, "bps", "en")).toBe("12.5 kbit/s");
    expect(formatValue(1_234_000_000, "bps", "en")).toBe("1.23 Gbit/s");
    expect(formatValue(3_000_000, "bytes", "en")).toBe("3 MB");
  });

  it("uses the locale decimal separator", () => {
    expect(formatValue(12_500_000, "bps", "it")).toBe("12,5 Mbit/s");
    expect(formatValue(42.25, "%", "fr")).toBe("42,3%");
  });

  it("formats latency, signal and counters", () => {
    expect(formatValue(3.14, "ms", "en")).toBe("3.1 ms");
    expect(formatValue(120.4, "ms", "en")).toBe("120 ms");
    expect(formatValue(-67, "dBm", "en")).toBe("-67 dBm");
    expect(formatValue(1234, "count", "en")).toBe("1,234");
  });

  it("shows a dash for missing values", () => {
    expect(formatValue(null, "bps", "en")).toBe("—");
    expect(formatValue(Number.NaN, "%", "en")).toBe("—");
  });
});

describe("seriesStats", () => {
  it("ignores gaps and takes the last present value as current", () => {
    expect(seriesStats([1, null, 5, 3, null])).toEqual({ current: 3, min: 1, avg: 3, max: 5 });
  });

  it("returns nulls for an empty series", () => {
    expect(seriesStats([null, null])).toEqual({ current: null, min: null, avg: null, max: null });
  });
});

describe("columns", () => {
  it("converts ISO timestamps to seconds and keeps gaps", () => {
    const points = [
      { timestamp: "2026-10-14T10:00:00+00:00", rx_bps: 100, tx_bps: null },
      { timestamp: "2026-10-14T10:05:00+00:00", rx_bps: null, tx_bps: 50 },
    ];
    const { timestamps, values } = columns(points, ["rx_bps", "tx_bps"]);
    expect(timestamps).toEqual([1_791_972_000, 1_791_972_300]);
    expect(values).toEqual([[100, null], [null, 50]]);
  });
});

describe("formatTick", () => {
  const ts = Date.UTC(2026, 9, 14, 13, 5) / 1000;
  it("adapts the label to the span and the language", () => {
    const hours = formatTick(ts, 3600, "it");
    expect(hours).toMatch(/^\d{2}:\d{2}$/);
    expect(formatTick(ts, 7 * 86400, "it")).toContain("\n");
    expect(formatTick(ts, 30 * 86400, "it")).toMatch(/^\d{2}\/10$/);
  });
});
