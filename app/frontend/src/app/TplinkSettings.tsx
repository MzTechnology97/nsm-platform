import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { formatDateTime } from "../lib/format";

type Value = boolean | number | string | null;

interface Field { group: "upgrades" | "network"; kind: "bool" | "uint" | "host"; value: Value; min: number | null; max: number | null; writable: boolean }

interface SettingsData {
  fields: Record<string, Field>;
  pages: { name: string; editable: boolean | null }[];
  admin_password: boolean;
  last_inform: string | null;
  can_edit: boolean;
  confirm_name: string;
  error: string | null;
}

const path = (id: string) => `/api/ui/v1/devices/${encodeURIComponent(id)}/acs/settings`;
const GROUPS = ["upgrades", "network"] as const;

/** Advanced TP-Link settings over TR-069 (ACS-10): only the changed values are sent, in one task. */
export function TplinkSettings({ deviceId }: { deviceId: string }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const key = ["device", deviceId, "acs-settings"];
  const query = useQuery({ queryKey: key, queryFn: () => api<SettingsData>(path(deviceId)) });
  const [values, setValues] = useState<Record<string, Value>>({});
  const [pages, setPages] = useState<Record<string, boolean>>({});
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const done = (data: SettingsData) => {
    client.setQueryData(key, data);
    setValues({});
    setPages({});
    setPassword("");
    setConfirm("");
  };
  const save = useMutation({
    mutationFn: () => api<SettingsData>(path(deviceId), {
      method: "POST",
      body: JSON.stringify({ values, pages, ...(password ? { admin_password: password, confirm } : {}) }),
    }),
    onSuccess: done,
  });
  const refresh = useMutation({ mutationFn: () => api<SettingsData>(`${path(deviceId)}/refresh`, { method: "POST" }), onSuccess: done });
  const data = query.data;
  if (!data || (!Object.keys(data.fields).length && !data.pages.length && !data.admin_password && !data.error)) return null;
  const editable = data.can_edit;
  const changed = Object.keys(values).length + Object.keys(pages).length + (password ? 1 : 0);
  const current = (name: string) => (name in values ? values[name] : data.fields[name]?.value ?? null);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };
  const input = (name: string, field: Field) => {
    const disabled = !editable || !field.writable;
    if (field.kind === "bool") {
      return (
        <label key={name} className="ui-with-brand">
          <input type="checkbox" checked={Boolean(current(name))} disabled={disabled} onChange={(event) => setValues({ ...values, [name]: event.target.checked })} />
          <span>{t(`tplinkSettings.fields.${name}`)}</span>
        </label>
      );
    }
    return (
      <label key={name}>
        <span>{t(`tplinkSettings.fields.${name}`)}{field.kind === "uint" && field.min != null ? ` (${field.min}–${field.max})` : ""}</span>
        <input type={field.kind === "uint" ? "number" : "text"} min={field.min ?? undefined} max={field.max ?? undefined} disabled={disabled}
          value={current(name) == null ? "" : String(current(name))} spellCheck={false}
          onChange={(event) => setValues({ ...values, [name]: field.kind === "uint" ? Number(event.target.value) : event.target.value })} />
      </label>
    );
  };
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <div>
          <h2>{t("tplinkSettings.title")}</h2>
          <p className="ui-muted">{t("tplinkSettings.help")}</p>
        </div>
        {editable && (
          <button type="button" className="ui-button ui-button-ghost" disabled={refresh.isPending} onClick={() => refresh.mutate()}>
            {refresh.isPending ? t("tplinkSettings.reading") : t("tplinkSettings.refresh")}
          </button>
        )}
      </div>
      {data.error && <div className="ui-alert ui-alert-warning">{data.error}</div>}
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit}>
        {GROUPS.map((group) => {
          const names = Object.keys(data.fields).filter((name) => data.fields[name].group === group);
          return names.length ? (
            <fieldset key={group} className="ui-tplink-settings-group">
              <legend>{t(`tplinkSettings.groups.${group}`)}</legend>
              {names.map((name) => input(name, data.fields[name]))}
            </fieldset>
          ) : null;
        })}
        {data.pages.length > 0 && (
          <fieldset className="ui-tplink-settings-group">
            <legend>{t("tplinkSettings.groups.pages")}</legend>
            <p className="ui-muted">{t("tplinkSettings.pagesHelp")}</p>
            {data.pages.map((page) => (
              <label key={page.name} className="ui-with-brand">
                <input type="checkbox" checked={page.name in pages ? pages[page.name] : Boolean(page.editable)} disabled={!editable}
                  onChange={(event) => setPages({ ...pages, [page.name]: event.target.checked })} />
                <span>{t(`tplinkSettings.pages.${page.name}`, { defaultValue: page.name })}</span>
              </label>
            ))}
          </fieldset>
        )}
        {data.admin_password && editable && (
          <fieldset className="ui-tplink-settings-group">
            <legend>{t("tplinkSettings.groups.admin")}</legend>
            <p className="ui-muted">{t("tplinkSettings.adminHelp")}</p>
            <label><span>{t("tplinkSettings.adminPassword")}</span>
              <input type="password" value={password} autoComplete="new-password" onChange={(event) => setPassword(event.target.value)} /></label>
            {password && (
              <label><span>{t("acsFirmware.confirm", { name: data.confirm_name })}</span>
                <input value={confirm} autoComplete="off" spellCheck={false} onChange={(event) => setConfirm(event.target.value)} /></label>
            )}
          </fieldset>
        )}
        {editable && (
          <button type="submit" className="ui-button" disabled={!changed || save.isPending || (Boolean(password) && confirm.trim() !== data.confirm_name)}>
            {t("tplinkSettings.save", { count: changed })}
          </button>
        )}
        {save.isSuccess && <small className="ui-live">{t("tplinkSettings.sent")}</small>}
        {(save.error || refresh.error) && <small className="ui-backup-error">{((save.error || refresh.error) as Error).message}</small>}
      </form>
      {data.last_inform && <small className="ui-muted">{t("tplinkAccess.readAt", { when: formatDateTime(data.last_inform, i18n.language) })}</small>}
    </section>
  );
}
