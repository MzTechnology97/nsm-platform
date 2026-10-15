import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { type FormEvent, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { type BaselineForm, type BaselineRow, type ControlMode, type ControlParams, useBaselineForm, useBaselines, useBaselineWrite } from "../api/security";
import { BrandIcon } from "../app/BrandIcon";
import { formatDateTime } from "../lib/format";
import { useFormError } from "./CustomerForm";

function TargetLabel({ row }: { row: BaselineRow }) {
  const { t } = useTranslation();
  const { target } = row;
  const scope = t(`complianceBaselines.scopes.${target.type}`, { defaultValue: target.type });
  if (target.type === "vendor") return <>{scope} · {target.vendor}</>;
  if (target.type === "customer") return <>{scope} · {target.customer?.name ?? "?"}</>;
  if (target.type === "site") return <>{scope} · {target.site ? `${target.site.customer ?? ""} · ${target.site.name}` : "?"}</>;
  if (target.type === "device") {
    return target.device ? (
      <span className="ui-with-brand">
        {scope} · <BrandIcon brand={target.device.brand} small />
        <Link to="/devices/$deviceId/compliance" params={{ deviceId: target.device.id }}>{target.device.name}</Link>
      </span>
    ) : <>{scope} · ?</>;
  }
  return <>{scope}</>;
}

/** Baselines from global to device; a more specific baseline overrides only the controls it sets. */
export function ComplianceBaselines() {
  const { t, i18n } = useTranslation();
  const query = useBaselines();
  const navigate = useNavigate();
  const errorText = useFormError();
  const createDefault = useBaselineWrite<undefined>("/default");
  const data = query.data;

  return (
    <section className="ui-page">
      <nav className="ui-breadcrumbs" aria-label={t("device.breadcrumbs")}>
        <Link to="/compliance">{t("compliance.title")}</Link>
        <span aria-hidden="true">›</span>
        <span>{t("complianceBaselines.title")}</span>
      </nav>
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("complianceBaselines.title")}</h1>
          <p>{t("complianceBaselines.subtitle")}</p>
        </div>
        {data?.can_manage && (
          <div className="ui-actions">
            <Link to="/compliance/baselines/new" className="ui-button ui-button-primary">{t("complianceBaselines.new")}</Link>
          </div>
        )}
      </header>
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <div className="ui-empty">{t("app.loading")}</div>
      ) : data.items.length ? (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead>
              <tr>{["baseline", "scope", "controls", "version", "state", "updated"].map((key) => <th key={key}><span className="ui-th">{t(`complianceBaselines.columns.${key}`)}</span></th>)}</tr>
            </thead>
            <tbody>
              {data.items.map((row) => (
                <tr key={row.id}>
                  <td>
                    {data.can_manage ? <Link to="/compliance/baselines/$baselineId/edit" params={{ baselineId: row.id }} className="ui-cell-title">{row.name}</Link> : <strong>{row.name}</strong>}
                    {row.description && <small className="ui-muted ui-block">{row.description}</small>}
                  </td>
                  <td><TargetLabel row={row} /></td>
                  <td>
                    <div className="ui-tag-list">
                      {row.controls.map((control) => (
                        <span key={control.id} className={control.enabled ? "ui-tag" : "ui-tag ui-tag-muted"} title={control.enabled ? t("complianceBaselines.modes.enabled") : t("complianceBaselines.modes.disabled")}>
                          {t(`compliance.controls.${control.id}`, { defaultValue: control.id })}{control.enabled ? "" : " (off)"}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td>v{row.version}</td>
                  <td><span className={row.enabled ? "ui-severity" : "ui-severity ui-severity-warning"}>{row.enabled ? t("complianceBaselines.enabled") : t("complianceBaselines.disabled")}</span></td>
                  <td className="ui-nowrap">{formatDateTime(row.updated_at, i18n.language)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ui-empty">
          <p>{t("complianceBaselines.empty")}</p>
          {data.can_manage && (
            <button type="button" className="ui-button ui-button-primary" disabled={createDefault.isPending}
              onClick={() => createDefault.mutate(undefined, { onSuccess: () => void navigate({ to: "/compliance" }) })}>
              {t("complianceBaselines.createDefault")}
            </button>
          )}
          {createDefault.error && <p className="ui-backup-error">{errorText(createDefault.error, "complianceBaselines.errors")}</p>}
        </div>
      )}
    </section>
  );
}

export function ComplianceBaselineNew() {
  return <BaselinePage />;
}

export function ComplianceBaselineEdit() {
  const { baselineId } = useParams({ from: "/compliance/baselines/$baselineId/edit" });
  return <BaselinePage id={baselineId} />;
}

function BaselinePage({ id }: { id?: string }) {
  const { t } = useTranslation();
  const query = useBaselineForm(id);
  const data = query.data;
  const title = data?.baseline?.name ?? t("complianceBaselines.newTitle");
  return (
    <section className="ui-page">
      <nav className="ui-breadcrumbs" aria-label={t("device.breadcrumbs")}>
        <Link to="/compliance">{t("compliance.title")}</Link>
        <span aria-hidden="true">›</span>
        <Link to="/compliance/baselines">{t("complianceBaselines.title")}</Link>
        <span aria-hidden="true">›</span>
        <span>{title}</span>
      </nav>
      <header className="ui-page-header">
        <h1>{title}</h1>
        <p>{data?.baseline ? t("complianceBaselines.versionHelp", { current: data.baseline.version, next: data.baseline.version + 1 }) : t("complianceBaselines.newHelp")}</p>
      </header>
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <div className="ui-empty">{t("app.loading")}</div>
      ) : (
        <BaselineEditor data={data} />
      )}
    </section>
  );
}

type ControlState = { mode: ControlMode; params: ControlParams };

function BaselineEditor({ data }: { data: BaselineForm }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const errorText = useFormError();
  const save = useBaselineWrite<Record<string, unknown>>("");
  const initial = data.baseline;
  const [form, setForm] = useState({
    name: initial?.name ?? "", description: initial?.description ?? "", scope_type: initial?.scope_type ?? "global", vendor: initial?.vendor ?? "",
    customer_id: initial?.customer_id ?? "", site_id: initial?.site_id ?? "", device_id: initial?.device_id ?? "", enabled: initial?.enabled ?? true,
  });
  const [controls, setControls] = useState<Record<string, ControlState>>(() =>
    Object.fromEntries(data.controls.map((control) => {
      const setting = data.settings[control.id];
      const mode: ControlMode = !setting ? "inherit" : setting.enabled === false ? "disabled" : "enabled";
      return [control.id, { mode, params: { ...control.defaults, ...(setting?.params ?? {}) } }];
    })),
  );
  const set = (key: keyof typeof form) => (event: { target: { value: string } }) => setForm({ ...form, [key]: event.target.value });
  const device = useMemo(() => data.devices.find((item) => item.id === form.device_id), [data.devices, form.device_id]);
  const setControl = (id: string, patch: Partial<ControlState>) => setControls({ ...controls, [id]: { ...controls[id], ...patch } });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate({ ...form, baseline_id: initial?.id ?? "", controls }, { onSuccess: () => void navigate({ to: "/compliance/baselines" }) });
  };

  return (
    <form onSubmit={submit}>
      <section className="ui-panel ui-admin-section">
        <div className="ui-admin-form">
          <label className="ui-admin-wide"><span>{t("complianceBaselines.name")}</span><input value={form.name} onChange={set("name")} required maxLength={160} /></label>
          <label><span>{t("complianceBaselines.scope")}</span>
            <select value={form.scope_type} onChange={set("scope_type")}>
              {data.scopes.map((scope) => <option key={scope} value={scope}>{t(`complianceBaselines.scopes.${scope}`, { defaultValue: scope })}</option>)}
            </select></label>
          {form.scope_type === "vendor" && (
            <label><span>{t("complianceBaselines.vendor")}</span>
              <select value={form.vendor} onChange={set("vendor")} required>
                <option value="">—</option>
                {data.vendors.map((vendor) => <option key={vendor} value={vendor}>{vendor}</option>)}
              </select></label>
          )}
          {form.scope_type === "customer" && (
            <label><span>{t("complianceBaselines.customer")}</span>
              <select value={form.customer_id} onChange={set("customer_id")} required>
                <option value="">—</option>
                {data.customers.map((customer) => <option key={customer.id} value={customer.id}>{customer.name}</option>)}
              </select></label>
          )}
          {form.scope_type === "site" && (
            <label className="ui-admin-wide"><span>{t("complianceBaselines.site")}</span>
              <select value={form.site_id} onChange={set("site_id")} required>
                <option value="">—</option>
                {data.sites.map((site) => <option key={site.id} value={site.id}>{site.customer ? `${site.customer} · ` : ""}{site.name}</option>)}
              </select></label>
          )}
          {form.scope_type === "device" && (
            <label className="ui-admin-wide"><span>{t("complianceBaselines.device")}</span>
              <select value={form.device_id} onChange={set("device_id")} required>
                <option value="">—</option>
                {data.devices.map((item) => <option key={item.id} value={item.id}>{item.customer ? `${item.customer.name} · ` : ""}{item.name}</option>)}
              </select></label>
          )}
          {form.scope_type === "device" && device && (
            <span className="ui-with-brand ui-policy-device"><BrandIcon brand={device.brand} small /> <span>{device.name}</span></span>
          )}
          <label className="ui-admin-full"><span>{t("complianceBaselines.description")}</span>
            <textarea rows={2} maxLength={2000} value={form.description} onChange={set("description")} /></label>
          <label className="ui-with-brand"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /> <span>{t("complianceBaselines.active")}</span></label>
        </div>
      </section>

      <section className="ui-panel ui-admin-section">
        <h2>{t("complianceBaselines.controlsTitle")}</h2>
        <p className="ui-muted">{t("complianceBaselines.controlsHelp")}</p>
        <div className="ui-baseline-controls">
          {data.controls.map((control) => {
            const state = controls[control.id];
            return (
              <div key={control.id} className={`ui-baseline-control is-${state.mode}`}>
                <div>
                  <strong>{t(`compliance.controls.${control.id}`, { defaultValue: control.title })}</strong> <span className="ui-tag">{control.category}</span>
                  <small className="ui-muted ui-block">{t(`complianceBaselines.descriptions.${control.id}`, { defaultValue: control.description })}</small>
                </div>
                <div className="ui-admin-form">
                  <label><span>{t("complianceBaselines.mode")}</span>
                    <select value={state.mode} onChange={(event) => setControl(control.id, { mode: event.target.value as ControlMode })}>
                      {(["inherit", "enabled", "disabled"] as const).map((mode) => <option key={mode} value={mode}>{t(`complianceBaselines.modes.${mode}`)}</option>)}
                    </select></label>
                  {state.mode !== "inherit" && Object.entries(control.defaults).map(([key, value]) =>
                    typeof value === "boolean" ? (
                      <label key={key} className="ui-with-brand">
                        <input type="checkbox" checked={Boolean(state.params[key])} onChange={(event) => setControl(control.id, { params: { ...state.params, [key]: event.target.checked } })} />
                        <span>{t(`complianceBaselines.params.${control.id}.${key}`, { defaultValue: control.params_help[key] ?? key })}</span>
                      </label>
                    ) : (
                      <label key={key}><span>{t(`complianceBaselines.params.${control.id}.${key}`, { defaultValue: control.params_help[key] ?? key })}</span>
                        <input type="number" min={1} max={3650} required value={Number(state.params[key])}
                          onChange={(event) => setControl(control.id, { params: { ...state.params, [key]: Number(event.target.value) } })} /></label>
                    ),
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <div className="ui-row-actions ui-policy-actions">
        {save.error && <span className="ui-backup-error">{errorText(save.error, "complianceBaselines.errors")}</span>}
        <Link to="/compliance/baselines" className="ui-button ui-button-ghost">{t("complianceBaselines.cancel")}</Link>
        <button type="submit" className="ui-button ui-button-primary" disabled={save.isPending}>{t("complianceBaselines.save")}</button>
      </div>
    </form>
  );
}
