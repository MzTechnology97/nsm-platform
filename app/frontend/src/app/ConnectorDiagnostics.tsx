import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { formatDateTime } from "../lib/format";

type FieldState = "ok" | "partial" | "missing";

interface DiagnosticField {
  field: string;
  expected: string[];
  found: number;
  rows: number;
  path: string | null;
  type: string | null;
  state: FieldState;
}

interface DiagnosticEndpoint {
  name: string;
  status: number | null;
  rows: number;
  error: string | null;
  fields: DiagnosticField[];
  keys: string[];
}

interface DiagnosticReport {
  provider: string;
  at: string;
  nsm_version: string;
  endpoints: DiagnosticEndpoint[];
  missing: string[];
  failed_endpoints: string[];
  text: string;
}

const STATE_TONE: Record<FieldState, string> = { ok: "online", partial: "warning", missing: "offline" };

/** Compatibility report of a connector: GET-only probes of the real console, expected fields found or missing, nothing but key names in it. */
export function ConnectorDiagnostics({ provider }: { provider: "uisp" | "cnmaestro" }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const key = ["connector-diagnostics", provider];
  const query = useQuery({ queryKey: key, queryFn: () => api<{ report: DiagnosticReport | null }>(`/api/ui/v1/admin/integrations/${provider}/diagnostics`) });
  const run = useMutation({
    mutationFn: () => api<{ report: DiagnosticReport }>(`/api/ui/v1/admin/integrations/${provider}/diagnostics`, { method: "POST" }),
    onSuccess: (data) => client.setQueryData(key, data),
  });
  const [copied, setCopied] = useState(false);
  const report = query.data?.report ?? null;
  const copy = async () => {
    if (!report) return;
    try {
      await navigator.clipboard.writeText(report.text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2500);
    } catch {
      setCopied(false);
    }
  };
  return (
    <section className="ui-panel ui-admin-section ui-diagnostics">
      <div className="ui-page-header-row">
        <div>
          <h2>{t("connectorDiagnostics.title")}</h2>
          <p className="ui-muted">{t("connectorDiagnostics.help")}</p>
        </div>
        <div className="ui-row-actions">
          {report && <button type="button" className="ui-button ui-button-ghost" onClick={() => void copy()}>{copied ? t("connectorDiagnostics.copied") : t("connectorDiagnostics.copy")}</button>}
          <button type="button" className="ui-button" disabled={run.isPending} onClick={() => run.mutate()}>
            {run.isPending ? t("connectorDiagnostics.running") : t("connectorDiagnostics.run")}
          </button>
        </div>
      </div>
      {run.error && <div className="ui-alert ui-alert-danger">{(run.error as Error).message}</div>}
      {!report ? (
        <div className="ui-empty">{t("connectorDiagnostics.none")}</div>
      ) : (
        <>
          <p className="ui-muted">{t("connectorDiagnostics.at", { when: formatDateTime(report.at, i18n.language, true), version: report.nsm_version })}</p>
          <div className="ui-chips">
            {report.endpoints.map((endpoint) => (
              <span key={endpoint.name} className={`ui-status ui-status-${endpoint.status === 200 ? "online" : "offline"}`} title={endpoint.error ?? undefined}>
                <code>{endpoint.name}</code> · {endpoint.status ?? "—"}
              </span>
            ))}
          </div>
          {report.missing.length > 0 ? (
            <div className="ui-alert ui-alert-warning">{t("connectorDiagnostics.missing", { count: report.missing.length, fields: report.missing.join(", ") })}</div>
          ) : (
            <div className="ui-alert">{t("connectorDiagnostics.allFound")}</div>
          )}
          {report.endpoints.filter((endpoint) => endpoint.fields.length || endpoint.error).map((endpoint) => (
            <details key={endpoint.name} className="ui-diagnostics-endpoint" open={endpoint.fields.some((field) => field.state !== "ok")}>
              <summary><code>{endpoint.name}</code> · HTTP {endpoint.status ?? "—"} · {t("connectorDiagnostics.rows", { count: endpoint.rows })}</summary>
              {endpoint.error && <p className="ui-backup-error">{endpoint.error}</p>}
              {endpoint.fields.length > 0 && (
                <div className="ui-data-wrap">
                  <table className="ui-data-table">
                    <thead>
                      <tr>
                        {(["field", "state", "found", "key"] as const).map((column) => <th key={column}><span className="ui-th">{t(`connectorDiagnostics.columns.${column}`)}</span></th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {endpoint.fields.map((field) => (
                        <tr key={field.field}>
                          <td>{field.field}</td>
                          <td><span className={`ui-status ui-status-${STATE_TONE[field.state]}`}>{t(`connectorDiagnostics.states.${field.state}`)}</span></td>
                          <td>{field.found}/{field.rows}</td>
                          <td><code>{field.path ? `${field.path} (${field.type})` : field.expected.join(" | ")}</code></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {endpoint.keys.length > 0 && (
                <details className="ui-diagnostics-keys">
                  <summary>{t("connectorDiagnostics.keys", { count: endpoint.keys.length })}</summary>
                  <pre className="ui-code-block">{endpoint.keys.join("\n")}</pre>
                </details>
              )}
            </details>
          ))}
        </>
      )}
    </section>
  );
}
