import { Link, useParams } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import { type LifecycleTab, useDeviceLifecycle, useLifecycleAction } from "../api/deviceTabs";
import { BrandIcon } from "../app/BrandIcon";
import { formatDate, formatDateTime } from "../lib/format";
import { Loading } from "../app/States";


function message(error: unknown) {
  return error instanceof ApiError ? error.message : (error as Error | null)?.message;
}

/** Device lifecycle: value and origin (catalog or manual), catalog correlation, and the decision taken on an EOL/EOS device. */
export function DeviceLifecycle() {
  const { t, i18n } = useTranslation();
  const { deviceId } = useParams({ from: "/devices/$deviceId/lifecycle" });
  const query = useDeviceLifecycle(deviceId);
  const data = query.data;
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <Loading />;
  const device = data.device;
  return (
    <>
      <section className="ui-panel ui-admin-section">
        <div className="ui-page-header-row">
          <div>
            <h2>{t("deviceLifecycle.title")}</h2>
            <p className="ui-muted">{t("deviceLifecycle.help", { vendor: device.vendor ?? "—", model: device.model ?? t("deviceLifecycle.noModel"),
              origin: t(`deviceLifecycle.matches.${device.match ?? "none"}`, { defaultValue: device.match ?? "" }) })}</p>
          </div>
          <Link to="/lifecycle">{t("deviceLifecycle.fleet")}</Link>
        </div>
        <div className="ui-metrics">
          <div className={`ui-metric${device.status === "eol" ? " ui-metric-danger" : device.status === "eos" ? " ui-metric-warning" : ""}`}>
            <span>{t("deviceLifecycle.status")}</span><strong>{t(`lifecycle.status.${device.status ?? "unknown"}`, { defaultValue: device.status ?? "—" })}</strong></div>
          <div className="ui-metric"><span>EOL</span><strong>{formatDate(device.eol_date, i18n.language)}</strong></div>
          <div className="ui-metric"><span>EOS</span><strong>{formatDate(device.eos_date, i18n.language)}</strong></div>
          <div className="ui-metric"><span>{t("deviceLifecycle.source")}</span><strong>{device.source ?? "—"}</strong>
            <small>{device.verified_at ? t("deviceLifecycle.verified", { when: formatDate(device.verified_at, i18n.language) }) : null}</small></div>
        </div>
      </section>
      {data.remediation && <Remediation deviceId={deviceId} data={data} />}
      <div className="ui-two-columns">
        <Catalog deviceId={deviceId} data={data} />
        {data.can_manage && <ManualValue deviceId={deviceId} data={data} />}
      </div>
    </>
  );
}

function Catalog({ deviceId, data }: { deviceId: string; data: LifecycleTab }) {
  const { t, i18n } = useTranslation();
  const useCatalog = useLifecycleAction(deviceId, "lifecycle/catalog");
  const { correlation } = data;
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("deviceLifecycle.catalogTitle")}</h2>
      <p>
        {correlation.match === "catalog" && correlation.record
          ? t("deviceLifecycle.catalogMatch", { model: correlation.record.model, source: correlation.record.source ?? "—", when: formatDate(correlation.record.evidence_date, i18n.language) })
          : correlation.match === "ambiguous"
            ? t("deviceLifecycle.catalogAmbiguous", { candidates: correlation.candidates.join(", "), model: data.device.model ?? "" })
            : correlation.match === "no_record" ? t("deviceLifecycle.catalogMissing", { model: data.device.model ?? "" }) : t("deviceLifecycle.catalogNoModel")}
      </p>
      {correlation.match !== "catalog" && (
        correlation.match === "no_record" && data.device.model && data.can_manage
          ? <Link to="/lifecycle/catalog/new" search={{ vendor: data.device.vendor ?? undefined, model: data.device.model }}>{t("deviceLifecycle.addToCatalog")}</Link>
          : <Link to="/lifecycle/catalog">{t("deviceLifecycle.openCatalog")}</Link>
      )}
      {data.device.match === "manual" && data.can_manage && (
        <div className="ui-row-actions">
          <button type="button" className="ui-button ui-button-ghost" disabled={useCatalog.isPending} onClick={() => useCatalog.mutate(undefined)}>{t("deviceLifecycle.useCatalog")}</button>
          {useCatalog.error && <small className="ui-backup-error">{message(useCatalog.error)}</small>}
        </div>
      )}
    </section>
  );
}

function ManualValue({ deviceId, data }: { deviceId: string; data: LifecycleTab }) {
  const { t } = useTranslation();
  const save = useLifecycleAction(deviceId, "lifecycle");
  const manual = data.device.match === "manual";
  const [form, setForm] = useState({
    lifecycle_status: manual ? data.device.status ?? "supported" : "supported", eol_date: manual ? data.device.eol_date ?? "" : "",
    eos_date: manual ? data.device.eos_date ?? "" : "", source: manual ? data.device.source ?? "" : "", evidence_date: data.today,
  });
  const set = (key: keyof typeof form) => (event: { target: { value: string } }) => setForm({ ...form, [key]: event.target.value });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(form);
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("deviceLifecycle.manualTitle")}</h2>
      <p className="ui-muted">{t("deviceLifecycle.manualHelp")}</p>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit}>
        <label><span>{t("deviceLifecycle.status")}</span>
          <select value={form.lifecycle_status} onChange={set("lifecycle_status")}>
            {data.states.map((state) => <option key={state} value={state}>{t(`lifecycle.status.${state}`, { defaultValue: state.toUpperCase() })}</option>)}
          </select></label>
        <label><span>EOL</span><input type="date" value={form.eol_date} onChange={set("eol_date")} /></label>
        <label><span>EOS</span><input type="date" value={form.eos_date} onChange={set("eos_date")} /></label>
        <label><span>{t("deviceLifecycle.source")}</span><input value={form.source} onChange={set("source")} required maxLength={255} placeholder={t("deviceLifecycle.sourcePlaceholder")} /></label>
        <label><span>{t("deviceLifecycle.evidence")}</span><input type="date" value={form.evidence_date} max={data.today} onChange={set("evidence_date")} required /></label>
        <button type="submit" className="ui-button" disabled={save.isPending}>{t("deviceLifecycle.saveManual")}</button>
        {save.isSuccess && <small className="ui-live">{t("deviceLifecycle.saved")}</small>}
        {save.error && <small className="ui-backup-error">{message(save.error)}</small>}
      </form>
    </section>
  );
}

function Remediation({ deviceId, data }: { deviceId: string; data: LifecycleTab }) {
  const { t, i18n } = useTranslation();
  const act = useLifecycleAction(deviceId, "lifecycle/remediation");
  const rem = data.remediation;
  const [action, setAction] = useState<"plan" | "exception" | "replaced" | "decommissioned">("plan");
  const [form, setForm] = useState({ target_date: "", exception_until: "", replacement_device_id: "", note: "" });
  if (!rem) return null;
  const set = (key: keyof typeof form) => (event: { target: { value: string } }) => setForm({ ...form, [key]: event.target.value });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    act.mutate({ action, ...form }, { onSuccess: () => setForm({ target_date: "", exception_until: "", replacement_device_id: "", note: "" }) });
  };
  return (
    <section className="ui-panel ui-admin-section" id="remediation">
      <div className="ui-page-header-row">
        <div>
          <h2>{t("deviceLifecycle.remediationTitle")}</h2>
          <p className="ui-muted">{t("deviceLifecycle.remediationHelp", { status: (data.device.status ?? "").toUpperCase() })}</p>
        </div>
        <span className={`ui-status ui-status-${rem.status === "open" ? "offline" : rem.status === "exception" ? "warning" : "online"}`}>
          {t(`lifecycle.remediation.${rem.status}`, { defaultValue: rem.status })}</span>
      </div>
      {rem.reason && <div className="ui-alert ui-alert-warning">{rem.reason}</div>}
      <dl className="ui-dl">
        {rem.status === "planned" && <><div><dt>{t("deviceLifecycle.targetDate")}</dt><dd>{formatDate(rem.target_date, i18n.language)}</dd></div>
          <div><dt>{t("deviceLifecycle.plan")}</dt><dd>{rem.plan_note ?? "—"}</dd></div></>}
        {rem.status === "exception" && <><div><dt>{t("deviceLifecycle.exceptionUntil")}</dt><dd>{formatDate(rem.exception_until, i18n.language)}</dd></div>
          <div><dt>{t("deviceLifecycle.reason")}</dt><dd>{rem.exception_reason ?? "—"}</dd></div></>}
        {rem.status === "replaced" && (
          <div><dt>{t("deviceLifecycle.replacedBy")}</dt><dd>{rem.replacement ? (
            <span className="ui-with-brand"><BrandIcon brand={rem.replacement.brand} small />
              <Link to="/devices/$deviceId" params={{ deviceId: rem.replacement.id }}>{rem.replacement.name}</Link></span>) : t("deviceLifecycle.removed")}</dd></div>
        )}
        {rem.status === "decommissioned" && <div><dt>{t("deviceLifecycle.decommission")}</dt><dd>{rem.plan_note ?? "—"}</dd></div>}
      </dl>
      {data.can_manage && (
        <form className="ui-admin-form" onSubmit={submit}>
          <label><span>{t("deviceLifecycle.decision")}</span>
            <select value={action} onChange={(event) => setAction(event.target.value as typeof action)}>
              {(["plan", "exception", "replaced", "decommissioned"] as const).map((key) => <option key={key} value={key}>{t(`deviceLifecycle.actions.${key}`)}</option>)}
            </select></label>
          {action === "plan" && <label><span>{t("deviceLifecycle.targetDate")}</span><input type="date" value={form.target_date} min={data.today} onChange={set("target_date")} required /></label>}
          {action === "exception" && (
            <label><span>{t("deviceLifecycle.exceptionUntil")}</span>
              <input type="date" value={form.exception_until} min={data.today} max={data.max_exception} onChange={set("exception_until")} required /></label>
          )}
          {action === "replaced" && (
            <label><span>{t("deviceLifecycle.replacement")}</span>
              <select value={form.replacement_device_id} onChange={set("replacement_device_id")} required>
                <option value="" disabled>—</option>
                {data.replacement_candidates.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.name}{candidate.model ? ` · ${candidate.model}` : ""}</option>)}
              </select></label>
          )}
          <label className="ui-admin-wide"><span>{t(`deviceLifecycle.notes.${action}`)}</span><input value={form.note} onChange={set("note")} maxLength={500} required={action !== "replaced"} /></label>
          <button type="submit" className="ui-button" disabled={act.isPending}>{t(`deviceLifecycle.submit.${action}`)}</button>
          {rem.status !== "open" && (
            <button type="button" className="ui-button ui-button-ghost" disabled={act.isPending} onClick={() => act.mutate({ action: "reopen" })}>{t("deviceLifecycle.reopen")}</button>
          )}
          {act.data?.message && <small className="ui-live ui-block">{act.data.message}</small>}
          {act.error && <small className="ui-backup-error ui-block">{message(act.error)}</small>}
        </form>
      )}
      {data.history.length > 0 && (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead><tr>{["when", "action", "status", "note"].map((key) => <th key={key}><span className="ui-th">{t(`deviceLifecycle.historyColumns.${key}`)}</span></th>)}</tr></thead>
            <tbody>
              {data.history.map((row, index) => (
                <tr key={`${row.created_at}-${index}`}>
                  <td className="ui-nowrap">{formatDateTime(row.created_at, i18n.language)}</td>
                  <td>{t(`deviceLifecycle.actions.${row.action}`, { defaultValue: row.action })}</td>
                  <td>{row.from_status ? t(`lifecycle.remediation.${row.from_status}`, { defaultValue: row.from_status }) : "—"} → {t(`lifecycle.remediation.${row.to_status}`, { defaultValue: row.to_status })}</td>
                  <td>{row.note ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
