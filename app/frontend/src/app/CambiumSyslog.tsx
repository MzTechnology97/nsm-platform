import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../api/client";
import { formatDateTime } from "../lib/format";

type Mode = "key" | "customer_name" | "address";
interface Change { param: string; before: unknown; after: unknown }
interface CambiumSyslogState {
  target: string | null; port: number; modes: Mode[]; default_mode: Mode; name: string; customer: string | null;
  credentials: "device" | "shared" | "none"; management_ip: string | null;
  state: { mode: Mode; server: string; port: number; applied_at: string; by: string | null; status: string; restored_at?: string | null;
    changes?: Change[]; unverified?: string[]; error?: string } | null;
  can_restore: boolean; can_configure: boolean;
}
interface Preview { mode: Mode; server: string; port: number; changes: Change[]; plan_id: string }

function message(error: unknown) {
  return error instanceof ApiError ? error.message : (error as Error | null)?.message;
}

function shown(value: unknown) {
  return value === "" || value === null || value === undefined ? "—" : String(value);
}

/** Syslog tab of a Cambium ePMP: NSM sets the remote syslog over SSH (preview of the changed parameters, typed confirmation). */
export function CambiumSyslogPanel({ id }: { id: string }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["device", id, "syslog-cambium"], queryFn: () => api<CambiumSyslogState>(`/api/ui/v1/devices/${id}/syslog/cambium`), retry: false });
  const [mode, setMode] = useState<Mode | "">("");
  const [typed, setTyped] = useState("");
  const [plan, setPlan] = useState<Preview | null>(null);
  const done = () => { setTyped(""); setPlan(null); void client.invalidateQueries({ queryKey: ["device", id] }); };
  const preview = useMutation({
    mutationFn: (chosen: Mode) => api<Preview>(`/api/ui/v1/devices/${id}/syslog/cambium/preview`, { method: "POST", body: JSON.stringify({ mode: chosen }) }),
    onSuccess: (result) => setPlan(result),
  });
  const configure = useMutation({
    mutationFn: (body: { mode: Mode; confirmation: string; plan_id: string }) =>
      api(`/api/ui/v1/devices/${id}/syslog/cambium`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: done,
  });
  const restore = useMutation({
    mutationFn: (confirmation: string) => api(`/api/ui/v1/devices/${id}/syslog/cambium/restore`, { method: "POST", body: JSON.stringify({ confirmation }) }),
    onSuccess: done,
  });
  const data = query.data;
  if (!data) return null;
  const chosen = (mode || data.state?.mode || data.default_mode) as Mode;
  const ready = data.credentials !== "none" && !!data.management_ip && !!data.target;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (plan) configure.mutate({ mode: plan.mode, confirmation: typed, plan_id: plan.plan_id });
  };
  const busy = preview.isPending || configure.isPending || restore.isPending;
  const state = data.state;
  return (
    <section className="ui-panel">
      <div className="ui-panel-header">
        <div>
          <h2>{t("cambiumSyslog.title")}</h2>
          <p className="ui-muted">{t("cambiumSyslog.hint")}</p>
        </div>
      </div>
      <dl className="ui-dl">
        <div><dt>{t("cambiumSyslog.target")}</dt><dd className="ui-mono">{data.target ? `${data.target}:${data.port}` : t("cambiumSyslog.noTarget")}</dd></div>
        <div><dt>{t("cambiumSyslog.credentials")}</dt><dd>{t(`cambiumSyslog.credentialSource.${data.credentials}`)}</dd></div>
        <div><dt>{t("cambiumSyslog.current")}</dt><dd>{state
          ? t(`cambiumSyslog.status.${state.status}`, { mode: t(`cambiumSyslog.modes.${state.mode}.label`), when: formatDateTime(state.applied_at, i18n.language), by: state.by ?? "—" })
          : t("cambiumSyslog.never")}</dd></div>
        {state?.error && <div><dt>{t("cambiumSyslog.error")}</dt><dd>{state.error}</dd></div>}
        {!!state?.unverified?.length && <div><dt>{t("cambiumSyslog.unverified")}</dt><dd className="ui-mono">{state.unverified.join(", ")}</dd></div>}
      </dl>
      {data.can_configure && !ready && <p className="ui-alert ui-alert-warning">{t("cambiumSyslog.notReady")}</p>}
      {data.can_configure && ready && (
        <form className="ui-admin-form ui-admin-stack ui-danger-zone" onSubmit={submit}>
          <fieldset className="ui-admin-full">
            <legend>{t("cambiumSyslog.modeTitle")}</legend>
            {data.modes.map((key) => (
              <label key={key} className="ui-with-brand">
                <input type="radio" name={`cambium-syslog-mode-${id}`} value={key} checked={chosen === key} onChange={() => { setMode(key); setPlan(null); }} />
                <span>
                  <strong>{t(`cambiumSyslog.modes.${key}.label`)}</strong>{key === data.default_mode && ` · ${t("cambiumSyslog.recommended")}`}
                  <small className="ui-muted ui-block">{t(`cambiumSyslog.modes.${key}.help`, { customer: data.customer ?? "—" })}</small>
                </span>
              </label>
            ))}
          </fieldset>
          <div className="ui-row-actions">
            <button type="button" className="ui-button" disabled={busy} onClick={() => preview.mutate(chosen)}>
              {preview.isPending ? t("cambiumSyslog.reading") : t("cambiumSyslog.preview")}
            </button>
          </div>
          {plan && (plan.changes.length === 0 ? <p className="ui-alert ui-alert-success">{t("cambiumSyslog.nothing")}</p> : (
            <div className="ui-table-scroll ui-admin-full">
              <table className="ui-stats-table">
                <thead><tr><th>{t("cambiumSyslog.param")}</th><th>{t("cambiumSyslog.before")}</th><th>{t("cambiumSyslog.after")}</th></tr></thead>
                <tbody>
                  {plan.changes.map((change) => (
                    <tr key={change.param}><td className="ui-mono">{change.param}</td><td className="ui-mono">{shown(change.before)}</td><td className="ui-mono">{shown(change.after)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
          <p className="ui-muted">{t("cambiumSyslog.noReboot")}</p>
          <label><span>{t("cambiumSyslog.confirm", { name: data.name })}</span>
            <input value={typed} onChange={(event) => setTyped(event.target.value)} autoComplete="off" /></label>
          <div className="ui-row-actions">
            <button type="submit" className="ui-button ui-button-danger" disabled={busy || !plan || plan.changes.length === 0 || typed.trim() !== data.name}>
              {configure.isPending ? t("cambiumSyslog.working") : t("cambiumSyslog.configure")}
            </button>
            {data.can_restore && (
              <button type="button" className="ui-button ui-button-ghost" disabled={busy || typed.trim() !== data.name} onClick={() => restore.mutate(typed)}>
                {restore.isPending ? t("cambiumSyslog.working") : t("cambiumSyslog.restore")}
              </button>
            )}
          </div>
          {configure.isSuccess && <div className="ui-alert ui-alert-success">{t("cambiumSyslog.done")}</div>}
          {restore.isSuccess && <div className="ui-alert ui-alert-success">{t("cambiumSyslog.restored")}</div>}
          {(preview.error || configure.error || restore.error) && <div className="ui-alert ui-alert-danger">{message(preview.error ?? configure.error ?? restore.error)}</div>}
        </form>
      )}
    </section>
  );
}
