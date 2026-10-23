import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { type PolicyForm, type PolicyState, type ScheduleKind, type ScopeType, usePolicyForm, usePolicyWrite } from "../api/backupPolicies";
import { BrandIcon } from "../app/BrandIcon";
import { useFormError } from "./CustomerForm";
import { Loading } from "../app/States";

const SCOPES: ScopeType[] = ["global", "vendor", "customer", "site", "device"];
const CUSTOMER_SCOPES = new Set<ScopeType>(["customer", "site", "device"]);
const SCHEDULES: ScheduleKind[] = ["six_hour", "daily", "weekly", "monthly"];
const WEEKDAYS = [1, 2, 3, 4, 5, 6, 0];
const VENDOR_OPTIONS: Record<string, string[]> = {
  mikrotik: ["mikrotik_binary", "mikrotik_export"],
  ubiquiti: ["ubiquiti_connector_config"],
  "tp-link": ["tr069_config"],
  cambium: ["cambium_config"],
  generic: ["generic_snapshot"],
};
const COMMON_OPTIONS = ["pre_firmware", "verify_hash"];

/** New backup policy, optionally inside a customer (``?customer=``) and preset on one of its sites or devices. */
export function BackupPolicyNew() {
  const search = useSearch({ from: "/backups/policies/new" });
  return <PolicyPage params={search} />;
}

export function BackupPolicyEdit() {
  const { policyId } = useParams({ from: "/backups/policies/$policyId/edit" });
  return <PolicyPage params={{ policy: policyId }} />;
}

export function BackupPolicyClone() {
  const { policyId } = useParams({ from: "/backups/policies/$policyId/clone" });
  return <PolicyPage params={{ policy: policyId, clone: true }} />;
}

function PolicyPage({ params }: { params: Parameters<typeof usePolicyForm>[0] }) {
  const { t } = useTranslation();
  const query = usePolicyForm(params);
  const errorText = useFormError();
  const data = query.data;
  const title = data ? t(`backupPolicy.titles.${data.mode}`) : t("backupPolicy.titles.create");

  return (
    <section className="ui-page">
      <nav className="ui-breadcrumbs" aria-label={t("device.breadcrumbs")}>
        {data?.context ? (
          <>
            <Link to="/customers/$customerId" params={{ customerId: data.context.id }}>{data.context.name}</Link>
            <span aria-hidden="true">›</span>
            <Link to="/backups" search={{ customer: data.context.id, view: "all" }}>{t("nav.backups")}</Link>
          </>
        ) : (
          <Link to="/backups">{t("nav.backups")}</Link>
        )}
        <span aria-hidden="true">›</span>
        <span>{title}</span>
      </nav>
      <header className="ui-page-header">
        <h1>{title}</h1>
        <p>{data?.mode === "clone" && data.policy ? t("backupPolicy.cloneOf", { name: data.policy.name }) : t("backupPolicy.subtitle")}</p>
      </header>
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{errorText(query.error, "backupPolicy.errors")}</div>
      ) : !data ? (
        <Loading />
      ) : (
        <>
          <PolicyEditor key={`${data.mode}-${data.policy?.id ?? data.context?.id ?? "new"}-${data.policy?.enabled ?? ""}`} data={data} />
          {data.mode === "edit" && data.policy && <PolicyManage policy={data.policy} />}
        </>
      )}
    </section>
  );
}

function PolicyEditor({ data }: { data: PolicyForm }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const errorText = useFormError();
  const [form, setForm] = useState<PolicyState>(data.state);
  const editing = data.mode === "edit" && data.policy;
  const save = usePolicyWrite<Record<string, unknown>>(editing ? `/${data.policy!.id}` : "", editing ? "PUT" : "POST");
  const set = <K extends keyof PolicyState>(key: K, value: PolicyState[K]) => setForm((previous) => ({ ...previous, [key]: value }));

  const sites = useMemo(() => data.sites.filter((site) => site.customer_id === form.customer_id), [data.sites, form.customer_id]);
  const devices = useMemo(() => data.devices.filter((device) => device.customer_id === form.customer_id), [data.devices, form.customer_id]);
  const device = data.devices.find((item) => item.id === form.device_id);
  // Keep site/device consistent with the chosen customer.
  useEffect(() => {
    if (form.site_id && !sites.some((site) => site.id === form.site_id)) set("site_id", "");
    if (form.device_id && !devices.some((item) => item.id === form.device_id)) set("device_id", "");
  }, [sites, devices, form.site_id, form.device_id]);

  const vendors = form.scope_type === "vendor" ? [form.vendor ?? "mikrotik"] : form.scope_type === "device" && device ? [device.vendor] : null;
  const shownVendors = (vendors ?? Object.keys(VENDOR_OPTIONS)).filter((vendor) => vendor in VENDOR_OPTIONS);

  const back = data.context ? { to: "/backups" as const, search: { customer: data.context.id, view: "all" as const } } : { to: "/backups" as const };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const body: Record<string, unknown> = {
      name: form.name, description: form.description ?? "", is_enabled: form.is_enabled, scope_type: form.scope_type, vendor: form.vendor ?? "",
      customer_id: form.customer_id, site_id: form.site_id, device_id: form.device_id,
      schedule_kind: form.schedule_kind, backup_time: form.schedule_time, weekday: form.schedule_weekday, monthday: form.schedule_monthday,
      retention_daily: form.retention_daily, retention_weekly: form.retention_weekly, retention_monthly: form.retention_monthly, retry_count: form.retry_count,
      ...form.options,
    };
    if (data.context) body.context_customer_id = data.context.id;
    save.mutate(body, { onSuccess: () => void navigate(back) });
  };

  return (
    <form className="ui-policy-form" onSubmit={submit}>
      <section className="ui-panel ui-admin-section">
        <h2>{t("backupPolicy.sections.target")}</h2>
        <p className="ui-muted">{t("backupPolicy.sections.targetHelp")}</p>
        <div className="ui-admin-form">
          <label className="ui-admin-wide"><span>{t("backupPolicy.name")}</span>
            <input value={form.name} onChange={(event) => set("name", event.target.value)} required maxLength={160} placeholder={t("backupPolicy.namePlaceholder")} autoComplete="off" /></label>
          <label><span>{t("backupPolicy.state")}</span>
            <select value={form.is_enabled ? "1" : "0"} onChange={(event) => set("is_enabled", event.target.value === "1")}>
              <option value="1">{t("backupPolicy.enabled")}</option>
              <option value="0">{t("backupPolicy.paused")}</option>
            </select></label>
          <label className="ui-admin-full"><span>{t("backupPolicy.description")}</span>
            <input value={form.description ?? ""} onChange={(event) => set("description", event.target.value)} placeholder={t("backupPolicy.descriptionPlaceholder")} /></label>
          <label><span>{t("backupPolicy.scope")}</span>
            <select value={form.scope_type} onChange={(event) => set("scope_type", event.target.value as ScopeType)}>
              {SCOPES.map((scope) => (
                <option key={scope} value={scope} disabled={Boolean(data.context) && !CUSTOMER_SCOPES.has(scope)}>{t(`backupPolicy.scopes.${scope}`)}</option>
              ))}
            </select></label>
          {form.scope_type === "vendor" && (
            <label><span>{t("backupPolicy.vendor")}</span>
              <select value={form.vendor ?? "mikrotik"} onChange={(event) => set("vendor", event.target.value)}>
                {data.vendors.map((vendor) => <option key={vendor.key} value={vendor.key}>{vendor.label}</option>)}
              </select></label>
          )}
          {CUSTOMER_SCOPES.has(form.scope_type) && (
            <label><span>{t("backupPolicy.customer")}</span>
              <select value={form.customer_id} onChange={(event) => set("customer_id", event.target.value)} required disabled={Boolean(data.context)}>
                <option value="">{t("backupPolicy.choose")}</option>
                {data.customers.map((customer) => <option key={customer.id} value={customer.id}>{customer.name}</option>)}
              </select></label>
          )}
          {form.scope_type === "site" && (
            <label><span>{t("backupPolicy.site")}</span>
              <select value={form.site_id} onChange={(event) => set("site_id", event.target.value)} required>
                <option value="">{form.customer_id ? t("backupPolicy.chooseSite") : t("backupPolicy.chooseCustomerFirst")}</option>
                {sites.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}
              </select></label>
          )}
          {form.scope_type === "device" && (
            <label className="ui-admin-wide"><span>{t("backupPolicy.device")}</span>
              <select value={form.device_id} onChange={(event) => set("device_id", event.target.value)} required>
                <option value="">{form.customer_id ? t("backupPolicy.chooseDevice") : t("backupPolicy.chooseCustomerFirst")}</option>
                {devices.map((item) => <option key={item.id} value={item.id}>{item.name} · {vendorLabel(data, item.vendor)}</option>)}
              </select></label>
          )}
          {form.scope_type === "device" && device && (
            <span className="ui-with-brand ui-policy-device"><BrandIcon brand={device.brand} small /> <span>{device.name}</span></span>
          )}
        </div>
      </section>

      <section className="ui-panel ui-admin-section">
        <h2>{t("backupPolicy.sections.schedule")}</h2>
        <div className="ui-policy-choices" role="radiogroup" aria-label={t("backupPolicy.sections.schedule")}>
          {SCHEDULES.map((kind) => (
            <label key={kind} className={`ui-policy-choice${form.schedule_kind === kind ? " is-active" : ""}`}>
              <input type="radio" name="schedule_kind" value={kind} checked={form.schedule_kind === kind} onChange={() => set("schedule_kind", kind)} />
              <span><strong>{t(`backupPolicy.schedules.${kind}`)}</strong><small>{t(`backupPolicy.scheduleHelp.${kind}`)}</small></span>
            </label>
          ))}
        </div>
        <div className="ui-admin-form">
          {form.schedule_kind !== "six_hour" && (
            <label><span>{t("backupPolicy.time")}</span><input type="time" value={form.schedule_time} onChange={(event) => set("schedule_time", event.target.value)} required /></label>
          )}
          {form.schedule_kind === "weekly" && (
            <label><span>{t("backupPolicy.weekday")}</span>
              <select value={form.schedule_weekday} onChange={(event) => set("schedule_weekday", Number(event.target.value))}>
                {WEEKDAYS.map((day) => <option key={day} value={day}>{t(`backupPolicy.weekdays.${day}`)}</option>)}
              </select></label>
          )}
          {form.schedule_kind === "monthly" && (
            <label><span>{t("backupPolicy.monthday")}</span>
              <select value={form.schedule_monthday} onChange={(event) => set("schedule_monthday", Number(event.target.value))}>
                {Array.from({ length: 28 }, (_, index) => index + 1).map((day) => <option key={day} value={day}>{day}</option>)}
              </select></label>
          )}
        </div>
      </section>

      <section className="ui-panel ui-admin-section">
        <h2>{t("backupPolicy.sections.content")}</h2>
        <p className="ui-muted">{vendors ? t("backupPolicy.sections.contentHelp") : t("backupPolicy.autoMethod")}</p>
        <div className="ui-policy-vendors">
          {shownVendors.map((vendor) => (
            <fieldset key={vendor} className="ui-policy-vendor">
              <legend>{t(`backupPolicy.capabilities.${vendor}.title`)}</legend>
              <small className="ui-muted">{t(`backupPolicy.capabilities.${vendor}.help`)}</small>
              {VENDOR_OPTIONS[vendor].map((key) => <OptionBox key={key} name={key} form={form} onChange={set} />)}
            </fieldset>
          ))}
          <fieldset className="ui-policy-vendor">
            <legend>{t("backupPolicy.capabilities.common")}</legend>
            {COMMON_OPTIONS.map((key) => <OptionBox key={key} name={key} form={form} onChange={set} />)}
          </fieldset>
        </div>
      </section>

      <section className="ui-panel ui-admin-section">
        <h2>{t("backupPolicy.sections.retention")}</h2>
        <p className="ui-muted">{t("backupPolicy.sections.retentionHelp")}</p>
        <div className="ui-admin-form">
          {(["retention_daily", "retention_weekly", "retention_monthly"] as const).map((key) => (
            <label key={key}><span>{t(`backupPolicy.${key}`)}</span>
              <input type="number" min={0} value={form[key]} onChange={(event) => set(key, Number(event.target.value))} required /></label>
          ))}
          <label><span>{t("backupPolicy.retry_count")}</span>
            <input type="number" min={0} max={10} value={form.retry_count} onChange={(event) => set("retry_count", Number(event.target.value))} required /></label>
        </div>
      </section>

      <div className="ui-row-actions ui-policy-actions">
        {save.error && <span className="ui-backup-error">{errorText(save.error, "backupPolicy.errors")}</span>}
        <Link {...back} className="ui-button ui-button-ghost">{t("backupPolicy.cancel")}</Link>
        <button type="submit" className="ui-button ui-button-primary" disabled={save.isPending}>{t(`backupPolicy.submit.${data.mode}`)}</button>
      </div>
    </form>
  );
}

function vendorLabel(data: PolicyForm, vendor: string) {
  return data.vendors.find((item) => item.key === vendor)?.label ?? vendor;
}

function OptionBox({ name, form, onChange }: { name: string; form: PolicyState; onChange: <K extends keyof PolicyState>(key: K, value: PolicyState[K]) => void }) {
  const { t } = useTranslation();
  return (
    <label className="ui-policy-option">
      <input type="checkbox" checked={Boolean(form.options[name])} onChange={(event) => onChange("options", { ...form.options, [name]: event.target.checked })} />
      <span><strong>{t(`backupPolicy.optionLabels.${name}.title`)}</strong><small>{t(`backupPolicy.optionLabels.${name}.help`)}</small></span>
    </label>
  );
}

/** Pause/resume, copy and deletion (typed name) of an existing policy. */
function PolicyManage({ policy }: { policy: { id: string; name: string; enabled: boolean } }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const errorText = useFormError();
  const toggle = usePolicyWrite<undefined, { enabled: boolean }>(`/${policy.id}/toggle`);
  const remove = usePolicyWrite<{ confirm_name: string }, { deleted: boolean }>(`/${policy.id}/delete`);
  const [typed, setTyped] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    remove.mutate({ confirm_name: typed }, { onSuccess: () => void navigate({ to: "/backups" }) });
  };
  return (
    <div className="ui-two-columns ui-section-gap">
      <section className="ui-panel ui-admin-section">
        <h2>{t("backupPolicy.manageTitle")}</h2>
        <p className="ui-muted">{policy.enabled ? t("backupPolicy.activeHelp") : t("backupPolicy.pausedHelp")}</p>
        <div className="ui-row-actions ui-policy-manage">
          <button type="button" className="ui-button" disabled={toggle.isPending} onClick={() => toggle.mutate(undefined)}>
            {policy.enabled ? t("backupPolicy.pause") : t("backupPolicy.resume")}
          </button>
          <Link to="/backups/policies/$policyId/clone" params={{ policyId: policy.id }} className="ui-button ui-button-ghost">{t("backupPolicy.clone")}</Link>
        </div>
        {toggle.error && <small className="ui-backup-error">{errorText(toggle.error, "backupPolicy.errors")}</small>}
      </section>
      <section className="ui-panel ui-admin-section ui-danger-zone">
        <h2>{t("backupPolicy.deleteTitle")}</h2>
        <p className="ui-muted">{t("backupPolicy.deleteHelp")}</p>
        <form className="ui-admin-form ui-admin-stack" onSubmit={submit}>
          <label><span>{t("backupPolicy.confirmName", { name: policy.name })}</span>
            <input value={typed} onChange={(event) => setTyped(event.target.value)} autoComplete="off" required /></label>
          <button type="submit" className="ui-button ui-button-danger" disabled={remove.isPending || typed.trim() !== policy.name}>{t("customerForm.delete")}</button>
          {remove.error && <small className="ui-backup-error">{errorText(remove.error, "backupPolicy.errors")}</small>}
        </form>
      </section>
    </div>
  );
}
