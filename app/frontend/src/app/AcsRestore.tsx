import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../api/client";
import { formatDateTime } from "../lib/format";

interface RestoreRow {
  path: string;
  snapshot: string;
  current: string | null;
  type: string;
}

interface RestoreState {
  status: "requested" | "verifying" | "verified" | "mismatch" | "failed" | "unverified" | "unconfirmed";
  filename: string;
  parameters: { path: string; value: string }[];
  requested_at: string;
  completed_at?: string;
  by: string | null;
  started: boolean;
  fault?: string;
  mismatch?: { path: string; value: string | null }[];
}

interface RestorePreview {
  artifact: { id: string; filename: string; created_at: string | null };
  parameters: RestoreRow[];
  skipped: Record<"unchanged" | "missing" | "not_writable" | "excluded" | "invalid", number>;
  max: number;
  state: RestoreState | null;
  can_restore: boolean;
  confirm_name: string;
}

const RUNNING = ["requested", "verifying"];

function message(error: unknown) {
  return error instanceof ApiError ? error.message : (error as Error | null)?.message;
}

/** Restore part of a TR-069 snapshot: preview of the writable parameters that differ, ticked by the operator,
 *  confirmed with the device name; one setParameterValues task, then NSM reads the values back from the CPE. */
export function AcsRestore({ deviceId, artifactId }: { deviceId: string; artifactId: string }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [confirm, setConfirm] = useState("");
  const base = `/api/ui/v1/devices/${encodeURIComponent(deviceId)}/acs/restore`;
  const preview = useQuery({
    queryKey: ["device", deviceId, "acs-restore", artifactId],
    queryFn: () => api<RestorePreview>(`${base}?artifact_id=${encodeURIComponent(artifactId)}`),
    enabled: open,
    retry: false,
  });
  const running = RUNNING.includes(preview.data?.state?.status ?? "");
  const state = useQuery({
    queryKey: ["device", deviceId, "acs-restore-state"],
    queryFn: () => api<{ state: RestoreState | null }>(`${base}/state`),
    enabled: open && running,
    refetchInterval: 8000,
  });
  const run = useMutation({
    mutationFn: () => api<{ state: RestoreState }>(base, {
      method: "POST", body: JSON.stringify({ artifact_id: artifactId, parameters: [...chosen], confirm }),
    }),
    onSuccess: () => {
      setChosen(new Set());
      setConfirm("");
      void client.invalidateQueries({ queryKey: ["device", deviceId, "acs-restore", artifactId] });
    },
  });
  const current = state.data?.state ?? run.data?.state ?? preview.data?.state ?? null;
  if (!open) {
    return (
      <section className="ui-panel ui-admin-section">
        <div className="ui-page-header-row">
          <div>
            <h2>{t("acsRestore.title")}</h2>
            <p className="ui-muted">{t("acsRestore.intro")}</p>
          </div>
          <button type="button" className="ui-button ui-button-ghost" onClick={() => setOpen(true)}>{t("acsRestore.prepare")}</button>
        </div>
      </section>
    );
  }
  const data = preview.data;
  const toggle = (path: string) => {
    const next = new Set(chosen);
    if (next.has(path)) next.delete(path);
    else next.add(path);
    setChosen(next);
  };
  const busy = RUNNING.includes(current?.status ?? "");
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("acsRestore.title")}</h2>
      <p className="ui-muted">{t("acsRestore.help")}</p>
      {preview.error && <div className="ui-alert ui-alert-danger">{message(preview.error)}</div>}
      {current && (
        <div className={`ui-alert${current.status === "verified" ? " ui-alert-success" : ["failed", "mismatch", "unconfirmed", "unverified"].includes(current.status) ? " ui-alert-warning" : ""}`}>
          {t(`acsRestore.states.${current.status}`, { count: current.parameters.length, when: formatDateTime(current.completed_at ?? current.requested_at, i18n.language),
            fault: current.fault ?? "" })}
          {current.status === "requested" && !current.started && ` ${t("acsRestore.queued")}`}
          {current.mismatch && current.mismatch.length > 0 && (
            <ul className="ui-mono">{current.mismatch.map((row) => <li key={row.path}>{row.path} = {row.value ?? "—"}</li>)}</ul>
          )}
        </div>
      )}
      {!data ? (
        !preview.error && <p className="ui-muted">{t("app.loading")}</p>
      ) : (
        <>
          <p className="ui-muted">{t("acsRestore.skipped", data.skipped)}</p>
          {data.parameters.length === 0 ? (
            <div className="ui-empty">{t("acsRestore.nothing")}</div>
          ) : (
            <>
              <div className="ui-table-scroll">
                <table className="ui-stats-table">
                  <thead>
                    <tr>
                      <th>
                        <input type="checkbox" aria-label={t("acsRestore.all")} checked={chosen.size === data.parameters.length}
                          onChange={(event) => setChosen(event.target.checked ? new Set(data.parameters.map((row) => row.path)) : new Set())} />
                      </th>
                      <th>{t("acsRestore.columns.path")}</th>
                      <th>{t("acsRestore.columns.current")}</th>
                      <th>{t("acsRestore.columns.snapshot")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.parameters.map((row) => (
                      <tr key={row.path}>
                        <td><input type="checkbox" aria-label={row.path} checked={chosen.has(row.path)} onChange={() => toggle(row.path)} /></td>
                        <td className="ui-mono ui-break">{row.path}</td>
                        <td className="ui-mono ui-break">{row.current ?? "—"}</td>
                        <td className="ui-mono ui-break">{row.snapshot || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {data.can_restore ? (
                <form className="ui-admin-form" onSubmit={(event) => { event.preventDefault(); run.mutate(); }}>
                  <label>
                    <span>{t("acsRestore.confirm", { name: data.confirm_name, count: chosen.size })}</span>
                    <input value={confirm} onChange={(event) => setConfirm(event.target.value)} autoComplete="off" />
                  </label>
                  <div className="ui-row-actions">
                    <button type="submit" className="ui-button ui-button-danger"
                      disabled={busy || run.isPending || chosen.size === 0 || chosen.size > data.max || confirm.trim() !== data.confirm_name}>
                      {t("acsRestore.run", { count: chosen.size })}
                    </button>
                  </div>
                </form>
              ) : (
                <p className="ui-muted">{t("acsRestore.noPermission")}</p>
              )}
              {run.error && <div className="ui-alert ui-alert-danger">{message(run.error)}</div>}
            </>
          )}
        </>
      )}
    </section>
  );
}
