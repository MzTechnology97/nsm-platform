import { Link, useParams } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { type RestoreTest, type RestoreTestInput, useRecordRestoreTest, useRestoreTestForm, useRestoreTests } from "../api/backups";
import { formatBytes, formatDateTime, fromLocalInput } from "../lib/format";
import { useFormError } from "./CustomerForm";
import { Metric } from "./DeviceDetail";

const RESULT_TONE: Record<string, string> = { passed: "ui-severity", partial: "ui-severity ui-severity-warning", failed: "ui-severity ui-severity-high" };

function ResultBadge({ result }: { result: string }) {
  const { t } = useTranslation();
  return <span className={RESULT_TONE[result] ?? "ui-severity"}>{t(`backups.restore.${result}`, { defaultValue: result })}</span>;
}

function BackLinks({ deviceId, list = true }: { deviceId: string; list?: boolean }) {
  const { t } = useTranslation();
  return (
    <div className="ui-actions">
      {list && <Link to="/devices/$deviceId/backups/restore-tests" params={{ deviceId }} className="ui-button ui-button-ghost">{t("restoreTests.history")}</Link>}
      <Link to="/devices/$deviceId/backups" params={{ deviceId }} className="ui-button ui-button-ghost">{t("backupArtifact.back")}</Link>
    </div>
  );
}

/** Restore-test evidence of the device; the artifact identity survives the file's retention. */
export function DeviceRestoreTests() {
  const { t, i18n } = useTranslation();
  const { deviceId } = useParams({ from: "/devices/$deviceId/backups/restore-tests" });
  const query = useRestoreTests(deviceId);

  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!query.data) return <div className="ui-empty">{t("app.loading")}</div>;
  const tests = query.data.tests;

  return (
    <section className="ui-panel">
      <div className="ui-panel-header">
        <div>
          <h2>{t("restoreTests.title")}</h2>
          <p className="ui-muted">{t("restoreTests.help")}</p>
        </div>
        <BackLinks deviceId={deviceId} list={false} />
      </div>
      {tests.length ? (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead>
              <tr>
                <th>{t("restoreTests.columns.performed")}</th>
                <th>{t("restoreTests.columns.result")}</th>
                <th>{t("restoreTests.columns.artifact")}</th>
                <th>{t("restoreTests.columns.target")}</th>
                <th>{t("restoreTests.columns.integrity")}</th>
                <th>{t("restoreTests.columns.notes")}</th>
              </tr>
            </thead>
            <tbody>
              {tests.map((test) => (
                <tr key={test.id}>
                  <td className="ui-nowrap">
                    {formatDateTime(test.performed_at, i18n.language)}
                    {test.performed_by && <small className="ui-muted ui-block">{test.performed_by}</small>}
                  </td>
                  <td><ResultBadge result={test.result} /></td>
                  <td>
                    {test.artifact.live && test.artifact.id ? (
                      <Link to="/devices/$deviceId/backups/artifacts/$artifactId/restore-test" params={{ deviceId, artifactId: test.artifact.id }} className="ui-mono ui-break">
                        {test.artifact.filename}
                      </Link>
                    ) : (
                      <span className="ui-mono ui-break">{test.artifact.filename} <small className="ui-muted">{t("restoreTests.gone")}</small></span>
                    )}
                    <small className="ui-muted ui-block" title={test.artifact.sha256 ?? ""}>SHA-256 {test.artifact.sha256 ? `${test.artifact.sha256.slice(0, 14)}…` : "—"}</small>
                  </td>
                  <td>
                    {t(`restoreTests.methods.${test.method}`, { defaultValue: test.method })}
                    <small className="ui-muted ui-block">{targetLabel(test, t)}</small>
                  </td>
                  <td>{test.integrity_ok ? t("restoreTests.integrityOk") : <span className="ui-backup-error">{t("restoreTests.integrityKo")}</span>}</td>
                  <td className="ui-break">{test.notes ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ui-empty">{t("restoreTests.empty")}</div>
      )}
    </section>
  );
}

function targetLabel(test: RestoreTest, t: (key: string, options?: Record<string, unknown>) => string) {
  return test.target_version ? t("restoreTests.targetWithVersion", { target: test.target, version: test.target_version }) : test.target;
}

const EMPTY: RestoreTestInput = { method: "", target_label: "", target_routeros_version: "", result: "", performed_at: "", notes: "" };

/** One archived file: integrity re-checked now, outcome form (backup.execute) and its own history. */
export function DeviceRestoreTest() {
  const { t, i18n } = useTranslation();
  const { deviceId, artifactId } = useParams({ from: "/devices/$deviceId/backups/artifacts/$artifactId/restore-test" });
  const query = useRestoreTestForm(deviceId, artifactId);
  const record = useRecordRestoreTest(deviceId, artifactId);
  const errorText = useFormError();
  const [form, setForm] = useState<RestoreTestInput>(EMPTY);
  const set = (key: keyof RestoreTestInput) => (event: { target: { value: string } }) => setForm({ ...form, [key]: event.target.value });
  const data = query.data;

  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <div className="ui-empty">{t("app.loading")}</div>;
  const { artifact, integrity } = data;
  const yesNo = (value: boolean, yes: string, no: string) => (value ? t(yes) : t(no));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    record.mutate(
      { ...form, performed_at: form.performed_at ? (fromLocalInput(form.performed_at) ?? form.performed_at) : "" },
      { onSuccess: () => setForm(EMPTY) },
    );
  };

  return (
    <div className="ui-syslog">
      <section className="ui-panel">
        <div className="ui-panel-header">
          <div>
            <h2>{t("restoreTests.formTitle", { name: artifact.filename })}</h2>
            <p className="ui-muted">{t("restoreTests.formHelp")}</p>
          </div>
          <BackLinks deviceId={deviceId} />
        </div>
        <div className="ui-metrics">
          <Metric label={t("restoreTests.integrity")} value={integrity.ok ? t("restoreTests.intact") : t("restoreTests.notVerified")} tone={integrity.ok ? undefined : "danger"}
            hint={t("restoreTests.checkedNow")} />
          <Metric label={t("restoreTests.run")} value={formatDateTime(artifact.run_started_at, i18n.language)}
            hint={`${t(`backups.status.${artifact.run_status}`, { defaultValue: artifact.run_status })} · ${artifact.type}`} />
          <Metric label={t("restoreTests.file")} value={yesNo(integrity.file_present, "restoreTests.present", "restoreTests.missing")}
            hint={`${formatBytes(artifact.size, i18n.language)} · ${yesNo(integrity.size_matches, "restoreTests.sizeOk", "restoreTests.sizeKo")}`}
            tone={integrity.file_present ? undefined : "danger"} />
          <Metric label="SHA-256" value={yesNo(integrity.sha256_matches, "restoreTests.shaOk", "restoreTests.shaKo")} hint={artifact.sha256 ? `${artifact.sha256.slice(0, 16)}…` : "—"}
            tone={integrity.sha256_matches ? undefined : "danger"} />
          <Metric label={t("restoreTests.sourceVersion")} value={data.source_version ?? t("restoreTests.notDeclared")} />
        </div>
        {!integrity.ok && <div className="ui-alert ui-alert-warning">{t("restoreTests.notIntact")}</div>}
      </section>

      {data.can_record && (
        <section className="ui-panel ui-admin-section">
          <h2>{t("restoreTests.recordTitle")}</h2>
          <p className="ui-muted">{t("restoreTests.recordHelp")}</p>
          <form className="ui-admin-form" onSubmit={submit}>
            <label><span>{t("restoreTests.method")}</span>
              <select value={form.method} onChange={set("method")} required>
                <option value="" disabled>{t("restoreTests.choose")}</option>
                {data.methods.map((key) => <option key={key} value={key}>{t(`restoreTests.methods.${key}`, { defaultValue: key })}</option>)}
              </select></label>
            <label><span>{t("restoreTests.result")}</span>
              <select value={form.result} onChange={set("result")} required>
                <option value="" disabled>{t("restoreTests.choose")}</option>
                {data.results.map((key) => (
                  <option key={key} value={key} disabled={key === "passed" && !integrity.ok}>{t(`backups.restore.${key}`, { defaultValue: key })}</option>
                ))}
              </select></label>
            <label><span>{t("restoreTests.target")}</span><input value={form.target_label} onChange={set("target_label")} required maxLength={200} placeholder="CHR-LAB-01" /></label>
            <label><span>{t("restoreTests.targetVersion")}</span><input value={form.target_routeros_version} onChange={set("target_routeros_version")} maxLength={80} placeholder="7.20.7" /></label>
            <label><span>{t("restoreTests.performedAt")}</span><input type="datetime-local" value={form.performed_at} onChange={set("performed_at")} />
              <small className="ui-muted">{t("restoreTests.performedHelp")}</small></label>
            <label className="ui-admin-wide"><span>{t("restoreTests.notes")}</span>
              <textarea value={form.notes} onChange={set("notes")} rows={4} maxLength={4000} placeholder={t("restoreTests.notesPlaceholder")} /></label>
            <div className="ui-admin-wide ui-actions">
              <button type="submit" className="ui-button ui-button-primary" disabled={record.isPending}>{t("restoreTests.submit")}</button>
              {record.isSuccess && (
                <span className="ui-live">{t("restoreTests.recorded", { result: t(`backups.restore.${record.data.result}`, { defaultValue: record.data.result }) })}</span>
              )}
              {record.error && <span className="ui-backup-error">{errorText(record.error, "restoreTests.errors")}</span>}
            </div>
          </form>
        </section>
      )}

      <section className="ui-panel">
        <div className="ui-panel-header"><h2>{t("restoreTests.fileHistory")}</h2></div>
        {data.history.length ? (
          <div className="ui-data-wrap">
            <table className="ui-data-table">
              <thead>
                <tr>
                  <th>{t("restoreTests.columns.performed")}</th>
                  <th>{t("restoreTests.columns.result")}</th>
                  <th>{t("restoreTests.columns.target")}</th>
                  <th>{t("restoreTests.columns.notes")}</th>
                </tr>
              </thead>
              <tbody>
                {data.history.map((test) => (
                  <tr key={test.id}>
                    <td className="ui-nowrap">
                      {formatDateTime(test.performed_at, i18n.language)}
                      {test.performed_by && <small className="ui-muted ui-block">{test.performed_by}</small>}
                    </td>
                    <td><ResultBadge result={test.result} /></td>
                    <td>
                      {t(`restoreTests.methods.${test.method}`, { defaultValue: test.method })}
                      <small className="ui-muted ui-block">{targetLabel(test, t)}</small>
                    </td>
                    <td className="ui-break">{test.notes ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="ui-empty">{t("restoreTests.noFileHistory")}</div>
        )}
      </section>
    </div>
  );
}
