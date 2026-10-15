import { Link, useParams } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { type ComplianceResultDetail, useComplianceResult, useComplianceResultAction } from "../api/security";
import { BrandIcon } from "../app/BrandIcon";
import { formatDateTime } from "../lib/format";
import { useFormError } from "./CustomerForm";

const MARK = { pass: "✓", fail: "✗", unknown: "?", not_applicable: "–" } as const;

function plusDays(isoDate: string, days: number): string {
  const date = new Date(`${isoDate}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

/** One control on one device: evidence, operator handling (acknowledge, exception, revoke) and history. */
export function ComplianceResult() {
  const { t, i18n } = useTranslation();
  const { resultId } = useParams({ from: "/compliance/results/$resultId" });
  const query = useComplianceResult(resultId);
  const data = query.data;

  if (query.error) return <section className="ui-page"><div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div></section>;
  if (!data) return <section className="ui-page"><div className="ui-empty">{t("app.loading")}</div></section>;
  const title = t(`compliance.controls.${data.control_id}`, { defaultValue: data.control?.title ?? data.control_id });
  const state = data.exception ? t("complianceResult.inException") : t(`compliance.result.${data.status}`);

  return (
    <section className="ui-page">
      <nav className="ui-breadcrumbs" aria-label={t("device.breadcrumbs")}>
        <Link to="/compliance">{t("compliance.title")}</Link>
        <span aria-hidden="true">›</span>
        {data.device && <><Link to="/devices/$deviceId/compliance" params={{ deviceId: data.device.id }}>{data.device.name}</Link><span aria-hidden="true">›</span></>}
        <span>{title}</span>
      </nav>
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{title}</h1>
          <p className="ui-with-brand">
            {data.device && (
              <>
                <BrandIcon brand={data.device.brand} small />
                <Link to="/devices/$deviceId" params={{ deviceId: data.device.id }}>{data.device.name}</Link>
              </>
            )}
            {data.customer && <> · <Link to="/customers/$customerId" params={{ customerId: data.customer.id }}>{data.customer.name}</Link></>}
            {data.control && <> · {data.control.category}</>}
          </p>
        </div>
        <span className={`ui-status ui-status-${data.exception || data.status === "unknown" ? "warning" : data.status === "fail" ? "offline" : data.status === "pass" ? "online" : "pending"}`}>
          {MARK[data.status]} {state}
        </span>
      </header>

      <div className="ui-two-columns">
        <section className="ui-panel ui-admin-section">
          <h2>{t("complianceResult.outcome")}</h2>
          {data.control && <p className="ui-muted">{data.control.description}</p>}
          <dl className="ui-dl">
            <div><dt>{t("complianceResult.evidence")}</dt><dd>{data.evidence || "—"}</dd></div>
            <div><dt>{t("complianceResult.evaluated")}</dt><dd>{formatDateTime(data.evaluated_at, i18n.language)}</dd></div>
            <div><dt>{t("complianceResult.since")}</dt><dd>{formatDateTime(data.status_changed_at, i18n.language)}</dd></div>
            <div><dt>{t("complianceResult.baseline")}</dt>
              <dd>{data.baseline ? <a href={`/compliance/baselines/${data.baseline.id}/edit`}>v{data.baseline.version ?? "?"}</a> : "—"}</dd></div>
            {data.acknowledged && (
              <div><dt>{t("complianceResult.acknowledged")}</dt><dd>{data.acknowledged.by ?? "—"} · {formatDateTime(data.acknowledged.at, i18n.language)}</dd></div>
            )}
            {data.exception && (
              <>
                <div><dt>{t("complianceResult.exceptionUntil")}</dt><dd>{formatDateTime(data.exception.until, i18n.language)}</dd></div>
                <div><dt>{t("complianceResult.reason")}</dt><dd className="ui-break">{data.exception.reason}</dd></div>
              </>
            )}
          </dl>
        </section>
        {data.can_manage && data.status === "fail" && <Handling data={data} />}
      </div>

      <section className="ui-panel ui-section-gap">
        <h2>{t("complianceResult.history")}</h2>
        <p className="ui-muted">{t("complianceResult.historyHelp")}</p>
        {data.history.length ? (
          <div className="ui-data-wrap">
            <table className="ui-data-table">
              <thead>
                <tr>{["when", "action", "result", "who", "note"].map((key) => <th key={key}><span className="ui-th">{t(`complianceResult.columns.${key}`)}</span></th>)}</tr>
              </thead>
              <tbody>
                {data.history.map((entry, index) => (
                  <tr key={index}>
                    <td className="ui-nowrap">{formatDateTime(entry.at, i18n.language)}</td>
                    <td>{t(`complianceResult.actions.${entry.action}`, { defaultValue: entry.action })}</td>
                    <td>
                      {entry.from && entry.from !== entry.to ? `${t(`compliance.result.${entry.from}`, { defaultValue: entry.from })} → ` : ""}
                      {entry.to ? t(`compliance.result.${entry.to}`, { defaultValue: entry.to }) : "—"}
                    </td>
                    <td>{entry.by ?? <span className="ui-muted">{t("complianceResult.automatic")}</span>}</td>
                    <td className="ui-break">{entry.note ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="ui-empty">{t("complianceResult.noHistory")}</div>
        )}
      </section>
    </section>
  );
}

/** compliance.manage on a failing result; the evaluated status itself is never edited by hand. */
function Handling({ data }: { data: ComplianceResultDetail }) {
  const { t } = useTranslation();
  const errorText = useFormError();
  const acknowledge = useComplianceResultAction(data.id, "acknowledge");
  const grant = useComplianceResultAction(data.id, "exception");
  const revoke = useComplianceResultAction(data.id, "revoke");
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [until, setUntil] = useState(plusDays(data.today, 90));
  const [revokeNote, setRevokeNote] = useState("");
  const done = acknowledge.data?.message ?? grant.data?.message ?? revoke.data?.message;
  const submit = (event: FormEvent, run: () => void) => {
    event.preventDefault();
    run();
  };

  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("complianceResult.handling")}</h2>
      <p className="ui-muted">{t("complianceResult.handlingHelp")}</p>
      {done && <div className="ui-alert ui-alert-success">{t("complianceResult.saved")}</div>}
      {!data.acknowledged && (
        <form className="ui-admin-form ui-admin-stack" onSubmit={(event) => submit(event, () => acknowledge.mutate({ note }, { onSuccess: () => setNote("") }))}>
          <label><span>{t("complianceResult.ackNote")}</span>
            <textarea rows={2} maxLength={2000} value={note} onChange={(event) => setNote(event.target.value)} placeholder={t("complianceResult.ackPlaceholder")} /></label>
          <button type="submit" className="ui-button" disabled={acknowledge.isPending}>{t("complianceResult.acknowledge")}</button>
          {acknowledge.error && <small className="ui-backup-error">{errorText(acknowledge.error, "complianceResult.errors")}</small>}
        </form>
      )}
      {data.exception ? (
        <form className="ui-admin-form ui-admin-stack" onSubmit={(event) => submit(event, () => revoke.mutate({ note: revokeNote }, { onSuccess: () => setRevokeNote("") }))}>
          <label><span>{t("complianceResult.note")}</span>
            <textarea rows={2} maxLength={2000} value={revokeNote} onChange={(event) => setRevokeNote(event.target.value)} /></label>
          <button type="submit" className="ui-button ui-button-ghost" disabled={revoke.isPending}>{t("complianceResult.revoke")}</button>
          {revoke.error && <small className="ui-backup-error">{errorText(revoke.error, "complianceResult.errors")}</small>}
        </form>
      ) : (
        <>
          <h3>{t("complianceResult.exceptionTitle")}</h3>
          <form className="ui-admin-form ui-admin-stack" onSubmit={(event) => submit(event, () => grant.mutate({ reason, until }, { onSuccess: () => setReason("") }))}>
            <label><span>{t("complianceResult.reasonLabel")}</span>
              <textarea rows={3} maxLength={2000} required value={reason} onChange={(event) => setReason(event.target.value)} placeholder={t("complianceResult.reasonPlaceholder")} /></label>
            <label><span>{t("complianceResult.validUntil")}</span>
              <input type="date" value={until} min={plusDays(data.today, 1)} max={data.max_exception} onChange={(event) => setUntil(event.target.value)} required />
              <small className="ui-muted">{t("complianceResult.validUntilHelp")}</small></label>
            <button type="submit" className="ui-button ui-button-primary" disabled={grant.isPending}>{t("complianceResult.grant")}</button>
            {grant.error && <small className="ui-backup-error">{errorText(grant.error, "complianceResult.errors")}</small>}
          </form>
        </>
      )}
    </section>
  );
}
