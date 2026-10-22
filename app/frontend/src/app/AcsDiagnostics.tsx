import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { formatDateTime } from "../lib/format";

type Kind = "ping" | "traceroute";

interface PingResult { state: string; success: number | null; failure: number | null; avg_ms: number | null; min_ms: number | null; max_ms: number | null }
interface TraceResult { state: string; response_ms: number | null; hops: { hop: number; host: string | null; address: string | null; error: number | null; rtt_ms: number[] }[] }

interface Test {
  state: "requested" | "complete" | "error" | "timeout";
  target: string;
  requested_at: string;
  completed_at?: string | null;
  by: string | null;
  started: boolean;
  error?: string | null;
  result: PingResult | TraceResult | null;
}

interface AcsDiagnosticsData {
  linked: boolean;
  can_run: boolean;
  model: "tr181" | "tr098" | null;
  tests: { ping: Test | null; traceroute: Test | null };
}

const path = (id: string) => `/api/ui/v1/devices/${encodeURIComponent(id)}/acs/diagnostics`;

/** Ping and traceroute run by the CPE itself through GenieACS (ACS-06); follows a running test every few seconds. */
export function AcsDiagnostics({ deviceId }: { deviceId: string }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["device", deviceId, "acs-diagnostics"],
    queryFn: () => api<AcsDiagnosticsData>(path(deviceId)),
    refetchInterval: (state) => (Object.values(state.state.data?.tests ?? {}).some((test) => test?.state === "requested") ? 4000 : false),
  });
  const [kind, setKind] = useState<Kind>("ping");
  const [target, setTarget] = useState("");
  const run = useMutation({
    mutationFn: () => api<AcsDiagnosticsData>(`${path(deviceId)}/${kind}`, { method: "POST", body: JSON.stringify({ target }) }),
    onSuccess: (data) => client.setQueryData(["device", deviceId, "acs-diagnostics"], data),
  });
  const data = query.data;
  if (!data || !data.linked) return null;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    run.mutate();
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("acsDiagnostics.title")}</h2>
      <p className="ui-muted">{t("acsDiagnostics.help")}</p>
      {data.can_run && (
        <form className="ui-admin-form ui-inline-ip" onSubmit={submit}>
          <select value={kind} onChange={(event) => setKind(event.target.value as Kind)} aria-label={t("acsDiagnostics.kind")}>
            <option value="ping">{t("acsDiagnostics.kinds.ping")}</option>
            <option value="traceroute">{t("acsDiagnostics.kinds.traceroute")}</option>
          </select>
          <input value={target} required onChange={(event) => setTarget(event.target.value)} placeholder={t("acsDiagnostics.targetPlaceholder")}
            aria-label={t("acsDiagnostics.target")} spellCheck={false} />
          <button type="submit" className="ui-button" disabled={run.isPending || !target.trim()}>{t("acsDiagnostics.run")}</button>
        </form>
      )}
      {run.error && <div className="ui-alert ui-alert-danger">{(run.error as Error).message}</div>}
      {(["ping", "traceroute"] as const).map((key) => {
        const test = data.tests[key];
        if (!test) return null;
        return (
          <div key={key} className="ui-acs-test">
            <h3>{t(`acsDiagnostics.kinds.${key}`)} · <span className="ui-mono">{test.target}</span></h3>
            <p className="ui-muted">
              {t(`acsDiagnostics.states.${test.state}`)}
              {test.state === "requested" && !test.started ? ` · ${t("acsDiagnostics.waitingContact")}` : ""}
              {" · "}{formatDateTime(test.completed_at ?? test.requested_at, i18n.language)}{test.by ? ` · ${test.by}` : ""}
            </p>
            {test.error && <div className="ui-alert ui-alert-warning">{test.error}</div>}
            {test.result && key === "ping" && <PingView result={test.result as PingResult} />}
            {test.result && key === "traceroute" && <TraceView result={test.result as TraceResult} />}
          </div>
        );
      })}
    </section>
  );
}

function PingView({ result }: { result: PingResult }) {
  const { t } = useTranslation();
  const ms = (value: number | null) => (value == null ? "—" : `${value} ms`);
  return (
    <dl className="ui-dl">
      <div><dt>{t("acsDiagnostics.ping.received")}</dt><dd>{result.success ?? "—"} / {(result.success ?? 0) + (result.failure ?? 0) || "—"}</dd></div>
      <div><dt>{t("acsDiagnostics.ping.min")}</dt><dd>{ms(result.min_ms)}</dd></div>
      <div><dt>{t("acsDiagnostics.ping.avg")}</dt><dd>{ms(result.avg_ms)}</dd></div>
      <div><dt>{t("acsDiagnostics.ping.max")}</dt><dd>{ms(result.max_ms)}</dd></div>
      {!/^complete/i.test(result.state) && <div><dt>{t("acsDiagnostics.cpeState")}</dt><dd className="ui-mono">{result.state}</dd></div>}
    </dl>
  );
}

function TraceView({ result }: { result: TraceResult }) {
  const { t } = useTranslation();
  if (!result.hops.length) return <p className="ui-muted">{!/^complete/i.test(result.state) ? `${t("acsDiagnostics.cpeState")}: ${result.state}` : t("acsDiagnostics.noHops")}</p>;
  return (
    <div className="ui-table-scroll">
      <table className="ui-stats-table">
        <thead><tr><th>#</th><th>{t("acsDiagnostics.trace.host")}</th><th>{t("acsDiagnostics.trace.address")}</th><th>{t("acsDiagnostics.trace.rtt")}</th></tr></thead>
        <tbody>
          {result.hops.map((hop) => (
            <tr key={hop.hop}>
              <td>{hop.hop}</td>
              <td>{hop.host ?? "*"}</td>
              <td className="ui-mono">{hop.address ?? "—"}</td>
              <td>{hop.rtt_ms.length ? hop.rtt_ms.map((v) => `${v} ms`).join(" · ") : "*"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
