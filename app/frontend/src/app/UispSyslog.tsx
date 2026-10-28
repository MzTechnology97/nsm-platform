import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../api/client";
import { formatDateTime } from "../lib/format";

type Mode = "key" | "customer_name" | "address";
interface UispSyslogState {
  linked: boolean; target: string | null; port: number; modes: Mode[]; default_mode: Mode; name: string; customer: string | null;
  supported: boolean | null; system_log: { enabled: boolean | null; server: string | null; port: number | null } | null;
  state: { mode: Mode; server: string; port: number; applied_at: string; by: string | null; status: string; restored_at?: string | null } | null;
  can_restore: boolean; can_configure: boolean;
}

function message(error: unknown) {
  return error instanceof ApiError ? error.message : (error as Error | null)?.message;
}

/** Syslog tab of a Ubiquiti device linked to UISP: UISP writes the remote syslog on the CPE (configuration applied with a reboot). */
export function UispSyslogPanel({ id }: { id: string }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["device", id, "syslog-uisp"], queryFn: () => api<UispSyslogState>(`/api/ui/v1/devices/${id}/syslog/uisp`), retry: false });
  const [mode, setMode] = useState<Mode | "">("");
  const [typed, setTyped] = useState("");
  const done = () => { setTyped(""); void client.invalidateQueries({ queryKey: ["device", id] }); };
  const configure = useMutation({
    mutationFn: (body: { mode: Mode; confirmation: string }) => api(`/api/ui/v1/devices/${id}/syslog/uisp`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: done,
  });
  const restore = useMutation({
    mutationFn: (confirmation: string) => api(`/api/ui/v1/devices/${id}/syslog/uisp/restore`, { method: "POST", body: JSON.stringify({ confirmation }) }),
    onSuccess: done,
  });
  const data = query.data;
  if (!data || !data.linked) return null;
  const chosen = (mode || data.state?.mode || data.default_mode) as Mode;
  const live = data.system_log;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    configure.mutate({ mode: chosen, confirmation: typed });
  };
  const busy = configure.isPending || restore.isPending;
  return (
    <section className="ui-panel">
      <div className="ui-panel-header">
        <div>
          <h2>{t("uispSyslog.title")}</h2>
          <p className="ui-muted">{t("uispSyslog.hint")}</p>
        </div>
      </div>
      <dl className="ui-dl">
        <div><dt>{t("uispSyslog.target")}</dt><dd className="ui-mono">{data.target ? `${data.target}:${data.port}` : t("uispSyslog.noTarget")}</dd></div>
        <div><dt>{t("uispSyslog.inUisp")}</dt><dd>{data.supported === false ? t("uispSyslog.unsupported")
          : live?.enabled && live.server ? t("uispSyslog.liveOn", { server: live.server, port: live.port ?? data.port }) : t("uispSyslog.liveOff")}</dd></div>
        <div><dt>{t("uispSyslog.current")}</dt><dd>{data.state
          ? t(`uispSyslog.status.${data.state.status}`, { mode: t(`uispSyslog.modes.${data.state.mode}.label`), when: formatDateTime(data.state.applied_at, i18n.language), by: data.state.by ?? "—" })
          : t("uispSyslog.never")}</dd></div>
      </dl>
      {data.can_configure && data.supported !== false && (
        <form className="ui-admin-form ui-admin-stack ui-danger-zone" onSubmit={submit}>
          <fieldset className="ui-admin-full">
            <legend>{t("uispSyslog.modeTitle")}</legend>
            {data.modes.map((key) => (
              <label key={key} className="ui-with-brand">
                <input type="radio" name={`uisp-syslog-mode-${id}`} value={key} checked={chosen === key} onChange={() => setMode(key)} />
                <span>
                  <strong>{t(`uispSyslog.modes.${key}.label`)}</strong>{key === data.default_mode && ` · ${t("uispSyslog.recommended")}`}
                  <small className="ui-muted ui-block">{t(`uispSyslog.modes.${key}.help`, { customer: data.customer ?? "—" })}</small>
                </span>
              </label>
            ))}
          </fieldset>
          <p className="ui-alert ui-alert-warning">{t("uispSyslog.reboot")}</p>
          <label><span>{t("uispSyslog.confirm", { name: data.name })}</span>
            <input value={typed} onChange={(event) => setTyped(event.target.value)} autoComplete="off" /></label>
          <div className="ui-row-actions">
            <button type="submit" className="ui-button ui-button-danger" disabled={busy || typed.trim() !== data.name || !data.target}>
              {configure.isPending ? t("uispSyslog.working") : t("uispSyslog.configure")}
            </button>
            {data.can_restore && (
              <button type="button" className="ui-button ui-button-ghost" disabled={busy || typed.trim() !== data.name} onClick={() => restore.mutate(typed)}>
                {restore.isPending ? t("uispSyslog.working") : t("uispSyslog.restore")}
              </button>
            )}
          </div>
          {configure.isSuccess && <div className="ui-alert ui-alert-success">{t("uispSyslog.done")}</div>}
          {restore.isSuccess && <div className="ui-alert ui-alert-success">{t("uispSyslog.restored")}</div>}
          {(configure.error || restore.error) && <div className="ui-alert ui-alert-danger">{message(configure.error ?? restore.error)}</div>}
        </form>
      )}
    </section>
  );
}
