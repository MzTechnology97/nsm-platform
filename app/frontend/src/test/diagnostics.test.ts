import { describe, expect, it } from "vitest";

import type { DiagnosticJob } from "../api/diagnostics";
import i18n from "../i18n";
import { diagnosticSummary } from "../lib/diagnostics";

const job = (over: Partial<DiagnosticJob>): DiagnosticJob => ({
  id: "j", diagnostic: "ping", status: "success", created_at: null, completed_at: null, target: "192.0.2.1", source: null, query: null,
  error: null, result: null, classic_url: "", export_url: null, ...over,
});

describe("diagnosticSummary", () => {
  const it_ = i18n.getFixedT("it");
  const en = i18n.getFixedT("en");

  it("summarises each result kind in the user's language", () => {
    const ping = job({ result: { kind: "ping", replies: [], sent: 4, received: 3, loss: 25, min: 10, avg: 12.17, max: 14, jitter: 2 } });
    expect(diagnosticSummary(ping, it_, "it")).toBe("25% perdita · media 12,2 ms");
    expect(diagnosticSummary(ping, en, "en")).toBe("25% loss · avg 12.2 ms");
    const trace = job({ diagnostic: "traceroute", result: { kind: "traceroute", hops: [], reached: false, count: 3 } });
    expect(diagnosticSummary(trace, en, "en")).toBe("3 hops · destination not reached");
    const logs = job({ diagnostic: "logs", result: { kind: "logs", entries: [{ time: null, topics: "", message: "", level: "error" }], counts: { critical: 0, error: 1, warning: 0 } } });
    expect(diagnosticSummary(logs, en, "en")).toBe("1 event · 1 serious");
    expect(diagnosticSummary(job({ diagnostic: "dhcp_lookup", result: { kind: "dhcp", rows: [] } }), it_, "it")).toBe("nessuna lease");
  });

  it("reports running and failed jobs", () => {
    expect(diagnosticSummary(job({ status: "pending" }), en, "en")).toBe("running");
    expect(diagnosticSummary(job({ status: "failed", error: "RouterOS ping failed" }), en, "en")).toBe("RouterOS ping failed");
    expect(diagnosticSummary(job({ status: "expired" }), it_, "it")).toBe("non riuscita");
  });
});
