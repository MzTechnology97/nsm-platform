import { Link, useParams } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { type BackupFile, type BackupFilters, type BackupRun, type DeviceBackups as Backups, useBackups, useDeleteBackupFile, useRunBackup } from "../api/backups";
import { ApiError } from "../api/client";
import { formatValue } from "../charts/format";
import { describeCron } from "../lib/cron";
import { formatDateTime, formatRelative } from "../lib/format";
import { Metric } from "./DeviceDetail";
import { Loading } from "../app/States";

const KINDS: BackupFilters["kind"][] = ["all", "binary", "export"];
const STATUSES: BackupFilters["status"][] = ["all", "success", "failed"];

export function DeviceBackups() {
  const { t, i18n } = useTranslation();
  const { deviceId } = useParams({ from: "/devices/$deviceId/backups" });
  const [filters, setFilters] = useState<BackupFilters>({ kind: "all", status: "all" });
  const query = useBackups(deviceId, filters);
  const data = query.data;

  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <Loading />;
  const filtered = filters.kind !== "all" || filters.status !== "all";

  return (
    <div className="ui-syslog">
      <RunBar id={deviceId} data={data} />
      <div className="ui-metrics">
        <Metric
          label={t("backups.metrics.latest")}
          value={data.summary.latest_success ? formatRelative(data.summary.latest_success, i18n.language) : t("backups.never")}
          hint={data.summary.latest_success ? formatDateTime(data.summary.latest_success, i18n.language) : t("backups.noSuccess")}
          tone={data.summary.latest_success ? undefined : "warning"}
        />
        <Metric
          label={t("backups.metrics.policy")}
          value={data.policy?.name ?? t("backups.noPolicy")}
          hint={data.policy ? describeCron(data.policy.schedule, t, i18n.language) : t("backups.noPolicyHint")}
          tone={data.policy ? undefined : "warning"}
        />
        <Metric
          label={t("backups.metrics.archive")}
          value={t("backups.files", { count: data.summary.files })}
          hint={t("backups.archiveHint", { size: formatValue(data.summary.bytes, "bytes", i18n.language), count: data.summary.readable })}
        />
        <Metric
          label={t("backups.metrics.restore")}
          value={data.summary.restore ? t(`backups.restore.${data.summary.restore.result}`, { defaultValue: data.summary.restore.result }) : t("backups.never")}
          hint={data.summary.restore ? formatDateTime(data.summary.restore.performed_at, i18n.language) : t("backups.restore.none")}
          tone={data.summary.restore?.result === "failed" ? "danger" : data.summary.restore?.result === "partial" ? "warning" : undefined}
        />
      </div>
      <section className="ui-panel">
        <div className="ui-panel-header">
          <div>
            <h2>{t("backups.archive.title")}</h2>
            <p className="ui-muted">{t("backups.archive.hint")}</p>
          </div>
          <div className="ui-actions">
            <Link to="/devices/$deviceId/backups/restore-tests" params={{ deviceId }} className="ui-button ui-button-ghost">{t("backups.links.restoreTests")}</Link>
            {data.can_delete && data.policy ? (
              <Link to="/backups/policies/$policyId/edit" params={{ policyId: data.policy.id }} className="ui-button ui-button-ghost">{t("backups.links.editPolicy")}</Link>
            ) : data.can_delete ? (
              <Link to="/backups/policies/new" search={{ customer: data.customer_id, device: deviceId }} className="ui-button ui-button-ghost">{t("backupOverview.createPolicy")}</Link>
            ) : (
              <Link to="/backups" className="ui-button ui-button-ghost">{t("backups.links.policies")}</Link>
            )}
          </div>
        </div>
        <div className="ui-filter-bar">
          <select value={filters.kind} onChange={(event) => setFilters({ ...filters, kind: event.target.value as BackupFilters["kind"] })} aria-label={t("backups.filters.kind")}>
            {KINDS.map((value) => <option key={value} value={value}>{t(`backups.filters.kinds.${value}`)}</option>)}
          </select>
          <select value={filters.status} onChange={(event) => setFilters({ ...filters, status: event.target.value as BackupFilters["status"] })} aria-label={t("backups.filters.status")}>
            {STATUSES.map((value) => <option key={value} value={value}>{t(`backups.filters.statuses.${value}`)}</option>)}
          </select>
          {filtered && (
            <button type="button" className="ui-button ui-button-ghost" onClick={() => setFilters({ kind: "all", status: "all" })}>{t("backups.filters.reset")}</button>
          )}
        </div>
        {data.runs.length ? (
          <div className="ui-backup-runs">
            {data.runs.map((run) => <RunCard key={run.id} id={deviceId} run={run} canDelete={data.can_delete} />)}
          </div>
        ) : (
          <div className="ui-empty">{filtered ? t("backups.archive.noMatch") : t("backups.archive.empty")}</div>
        )}
      </section>
    </div>
  );
}

function RunBar({ id, data }: { id: string; data: Backups }) {
  const { t } = useTranslation();
  const run = useRunBackup(id);
  const error = run.error instanceof ApiError ? run.error : null;
  if (!data.run.can_execute) return null;
  return (
    <div className="ui-backup-bar">
      <div>
        <strong>{data.capability.method}</strong>
        {data.run.pending ? (
          <span className="ui-severity ui-severity-warning">{t("backups.run.pending")}</span>
        ) : data.run.blocker ? (
          <small className="ui-muted">{t(`backups.blocker.${data.run.blocker}`)}</small>
        ) : (
          <small className="ui-muted">{t("backups.run.ready")}</small>
        )}
      </div>
      <button type="button" className="ui-button ui-button-primary" disabled={!data.run.can_run || run.isPending} onClick={() => run.mutate()}>
        {run.isPending ? t("app.loading") : t("backups.run.now")}
      </button>
      {run.isSuccess && <div className="ui-alert ui-alert-success">{run.data.already ? t("backups.run.already") : t("backups.run.queued")}</div>}
      {error && <div className="ui-alert ui-alert-danger">{error.code && error.code !== "refused" ? t(`backups.blocker.${error.code}`) : error.message}</div>}
    </div>
  );
}

function RunCard({ id, run, canDelete }: { id: string; run: BackupRun; canDelete: boolean }) {
  const { t, i18n } = useTranslation();
  return (
    <article className="ui-backup-run">
      <header>
        <strong>{formatDateTime(run.started_at, i18n.language)}</strong>
        <span className={run.status === "success" ? "ui-status ui-status-online" : run.status === "failed" ? "ui-status ui-status-offline" : "ui-status ui-status-pending"}>
          {t(`backups.status.${run.status}`, { defaultValue: run.status })}
        </span>
        {run.backup_type && <small className="ui-muted">{run.backup_type}</small>}
      </header>
      {run.error && <p className="ui-backup-error">{run.error}</p>}
      {run.files.length ? (
        <table className="ui-log-table">
          <thead>
            <tr>
              <th>{t("backups.columns.file")}</th>
              <th>{t("backups.columns.size")}</th>
              <th>{t("backups.columns.sha")}</th>
              <th>{t("backups.columns.restore")}</th>
              <th className="ui-align-end">{t("backups.columns.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {run.files.map((file) => <FileRow key={file.id} id={id} file={file} canDelete={canDelete} />)}
          </tbody>
        </table>
      ) : (
        !run.error && <p className="ui-muted">{t("backups.archive.noFiles")}</p>
      )}
    </article>
  );
}

function FileRow({ id, file, canDelete }: { id: string; file: BackupFile; canDelete: boolean }) {
  const { t, i18n } = useTranslation();
  const remove = useDeleteBackupFile(id);
  const onDelete = () => {
    if (window.confirm(t("backups.deleteConfirm", { name: file.filename }))) remove.mutate(file.id);
  };
  return (
    <tr>
      <td>
        <span className={file.kind === "export" ? "ui-tag ui-tag-new" : "ui-tag"}>{file.kind === "export" ? ".rsc" : ".backup"}</span>{" "}
        <span className="ui-mono ui-break">{file.filename}</span>
      </td>
      <td className="ui-nowrap">{formatValue(file.size, "bytes", i18n.language)}</td>
      <td><code title={file.sha256 ?? ""}>{file.sha256 ? `${file.sha256.slice(0, 12)}…` : "—"}</code></td>
      <td>
        {file.restore ? (
          <span className={file.restore.result === "passed" ? "ui-severity" : file.restore.result === "failed" ? "ui-severity ui-severity-high" : "ui-severity ui-severity-warning"} title={formatDateTime(file.restore.performed_at, i18n.language)}>
            {t(`backups.restore.${file.restore.result}`, { defaultValue: file.restore.result })}
          </span>
        ) : "—"}
      </td>
      <td>
        <div className="ui-row-actions">
          {file.viewable && (
            <Link to="/devices/$deviceId/backups/artifacts/$artifactId" params={{ deviceId: id, artifactId: file.id }} className="ui-button ui-button-ghost ui-button-small">
              {t("backups.actions.view")}
            </Link>
          )}
          <a className="ui-button ui-button-ghost ui-button-small" href={file.download_url}>{t("backups.actions.download")}</a>
          <Link to="/devices/$deviceId/backups/artifacts/$artifactId/restore-test" params={{ deviceId: id, artifactId: file.id }} className="ui-button ui-button-ghost ui-button-small">
            {t("backups.actions.restore")}
          </Link>
          {canDelete && (
            <button type="button" className="ui-button ui-button-ghost ui-button-small ui-button-danger" disabled={remove.isPending} onClick={onDelete}>
              {t("backups.actions.delete")}
            </button>
          )}
        </div>
        {remove.error && <small className="ui-backup-error">{(remove.error as Error).message}</small>}
      </td>
    </tr>
  );
}
