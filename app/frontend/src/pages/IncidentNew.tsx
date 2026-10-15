import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { type IncidentForm, useCreateIncident, useIncidentForm } from "../api/incidents";
import { BrandIcon } from "../app/BrandIcon";
import { fromLocalInput, toLocalInput } from "../lib/format";
import { useFormError } from "./CustomerForm";

/** New incident: choose the customer (or arrive from a device), then title, severity, start and involved devices. */
export function IncidentNew() {
  const { t } = useTranslation();
  const search = useSearch({ from: "/incidents/new" });
  const navigate = useNavigate({ from: "/incidents/new" });
  const query = useIncidentForm(search);
  const data = query.data;

  return (
    <section className="ui-page">
      <nav className="ui-breadcrumbs" aria-label={t("device.breadcrumbs")}>
        <Link to="/incidents">{t("incidents.title")}</Link>
        <span aria-hidden="true">›</span>
        <span>{t("incidentNew.title")}</span>
      </nav>
      <header className="ui-page-header">
        <h1>{t("incidentNew.title")}</h1>
        <p>{t("incidentNew.subtitle")}</p>
      </header>
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <div className="ui-empty">{t("app.loading")}</div>
      ) : !data.customer ? (
        <section className="ui-panel ui-admin-section ui-form-narrow">
          <form className="ui-admin-form ui-admin-stack" onSubmit={(event) => event.preventDefault()}>
            <label><span>{t("incidentNew.customer")}</span>
              <select value="" onChange={(event) => event.target.value && void navigate({ search: { ...search, customer: event.target.value, device: undefined } })}>
                <option value="">{t("incidentNew.chooseCustomer")}</option>
                {data.customers.map((customer) => <option key={customer.id} value={customer.id}>{customer.name}</option>)}
              </select></label>
          </form>
        </section>
      ) : (
        <IncidentEditor key={data.customer.id} data={data} onChangeCustomer={() => void navigate({ search: { title: search.title, summary: search.summary, severity: search.severity } })} />
      )}
    </section>
  );
}

function IncidentEditor({ data, onChangeCustomer }: { data: IncidentForm; onChangeCustomer: () => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const errorText = useFormError();
  const create = useCreateIncident();
  const [form, setForm] = useState({
    title: data.prefill.title, summary: data.prefill.summary, severity: data.prefill.severity,
    started_at: toLocalInput(data.now), site_id: data.site_id ?? "",
  });
  const [devices, setDevices] = useState<Set<string>>(new Set(data.preselected));
  const set = (key: keyof typeof form) => (event: { target: { value: string } }) => setForm({ ...form, [key]: event.target.value });
  const toggle = (id: string) => {
    const next = new Set(devices);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setDevices(next);
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate(
      { ...form, started_at: fromLocalInput(form.started_at) ?? form.started_at, customer_id: data.customer!.id, device_ids: [...devices] },
      { onSuccess: (result) => void navigate({ to: "/incidents/$incidentId", params: { incidentId: result.id } }) },
    );
  };

  return (
    <form onSubmit={submit}>
      <section className="ui-panel ui-admin-section">
        <div className="ui-admin-form">
          <label><span>{t("incidentNew.customer")}</span><input value={data.customer!.name} readOnly />
            <small><button type="button" className="ui-link-button" onClick={onChangeCustomer}>{t("incidentNew.changeCustomer")}</button></small></label>
          <label><span>{t("incidentNew.site")}</span>
            <select value={form.site_id} onChange={set("site_id")}>
              <option value="">{t("incidentNew.noSite")}</option>
              {data.sites.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}
            </select></label>
          <label className="ui-admin-wide"><span>{t("incidentNew.titleLabel")}</span>
            <input value={form.title} onChange={set("title")} required maxLength={200} placeholder={t("incidentNew.titlePlaceholder")} /></label>
          <label><span>{t("incidentNew.severity")}</span>
            <select value={form.severity} onChange={set("severity")}>
              {data.severities.map((severity) => <option key={severity} value={severity}>{t(`incidents.severities.${severity}`, { defaultValue: severity })}</option>)}
            </select></label>
          <label><span>{t("incidentNew.started")}</span><input type="datetime-local" value={form.started_at} onChange={set("started_at")} required />
            <small className="ui-muted">{t("incidentNew.startedHelp")}</small></label>
          <label className="ui-admin-full"><span>{t("incidentNew.summary")}</span>
            <textarea rows={3} maxLength={4000} value={form.summary} onChange={set("summary")} placeholder={t("incidentNew.summaryPlaceholder")} /></label>
        </div>
      </section>

      <section className="ui-panel ui-admin-section">
        <h2>{t("incidentNew.devices")}</h2>
        <p className="ui-muted">{t("incidentNew.devicesHelp", { count: devices.size })}</p>
        {data.devices.length ? (
          <div className="ui-admin-form ui-incident-devices">
            {data.devices.map((device) => (
              <label key={device.id} className="ui-with-brand">
                <input type="checkbox" checked={devices.has(device.id)} onChange={() => toggle(device.id)} />
                <BrandIcon brand={device.brand} small />
                <span>
                  {device.name}
                  <small className="ui-muted ui-block">
                    {[device.model, t(`devices.statusLabel.${device.status}`, { defaultValue: device.status })].filter(Boolean).join(" · ")}
                  </small>
                </span>
              </label>
            ))}
          </div>
        ) : (
          <p className="ui-muted">{t("incidentNew.noDevices")}</p>
        )}
      </section>

      <div className="ui-row-actions ui-policy-actions">
        {create.error && <span className="ui-backup-error">{errorText(create.error, "incidentNew.errors")}</span>}
        <Link to="/incidents" className="ui-button ui-button-ghost">{t("incidentNew.cancel")}</Link>
        <button type="submit" className="ui-button ui-button-primary" disabled={create.isPending}>{t("incidentNew.submit")}</button>
      </div>
    </form>
  );
}
