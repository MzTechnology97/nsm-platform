import { useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { type AgentStatus, useAgent, useAgentReinstall, useAgentUpdate } from "../api/agent";
import { EnrollmentCommand } from "../app/EnrollmentCommand";
import { formatDateTime, formatRelative } from "../lib/format";
import { Metric } from "./DeviceDetail";
import { eventSource } from "../lib/events";
import { Loading } from "../app/States";

const TONE: Record<AgentStatus["installation"]["severity"], string> = {
  success: "ui-alert ui-alert-success",
  info: "ui-alert",
  warning: "ui-alert ui-alert-warning",
  critical: "ui-alert ui-alert-danger",
};

export function DeviceAgent() {
  const { t, i18n } = useTranslation();
  const { deviceId } = useParams({ from: "/devices/$deviceId/agent" });
  const query = useAgent(deviceId);
  const reinstall = useAgentReinstall(deviceId);
  const data = query.data;
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <Loading />;
  const { installation, heartbeat, update, credential } = data;
  const installTone = installation.severity === "critical" ? "danger" : installation.severity === "warning" ? "warning" : undefined;

  return (
    <div className="ui-syslog">
      <div className="ui-metrics">
        <Metric label={t("agent.metrics.installation")} value={t(`agent.install.${installation.key}.label`)} hint={t(`agent.install.${installation.key}.short`)} tone={installTone} />
        <Metric label={t("agent.metrics.transport")} value={(heartbeat.transport || "—").toUpperCase()} hint={heartbeat.enrollment_transport ?? "—"} />
        <Metric
          label={t("agent.metrics.version")}
          value={heartbeat.agent_version ?? "—"}
          hint={credential?.active ? t("agent.credential.active") : t("agent.credential.inactive")}
          tone={update.outdated ? "warning" : undefined}
        />
        <Metric
          label={t("agent.metrics.heartbeat")}
          value={heartbeat.last_seen ? formatRelative(heartbeat.last_seen, i18n.language) : "—"}
          hint={heartbeat.stale ? t("agent.heartbeat.stale") : t("agent.heartbeat.regular")}
          tone={heartbeat.stale ? "warning" : undefined}
        />
      </div>

      {installation.severity !== "success" && (
        <div className={TONE[installation.severity]}>
          <strong>{t(`agent.install.${installation.key}.label`)}.</strong> {t(`agent.install.${installation.key}.detail`)}
          {installation.severity === "critical" && <> {t("agent.install.checkRouter")} <code>/log print where message~"NSM|Script Error"</code></>}
        </div>
      )}

      <UpdatePanel id={deviceId} data={data} />

      <section className="ui-panel">
        <div className="ui-panel-header">
          <div>
            <h2>{t("agent.pipeline.title")}</h2>
            <p className="ui-muted">{t("agent.pipeline.hint")}</p>
          </div>
        </div>
        <ol className="ui-steps">
          {installation.steps.map((step) => (
            <li key={step.key} className={step.done ? "is-done" : undefined}>
              <span className="ui-step-mark" aria-hidden="true">{step.done ? "✓" : "…"}</span>
              <div>
                <strong>{t(`agent.pipeline.steps.${step.key}`)}</strong>
                <small className="ui-muted">
                  {step.at ? formatDateTime(step.at, i18n.language) : step.note ? t(`agent.pipeline.notes.${step.note}`, { defaultValue: step.note }) : "—"}
                </small>
              </div>
              <span className={step.done ? "ui-severity" : "ui-severity ui-severity-warning"}>{step.done ? t("agent.ok") : t("agent.waiting")}</span>
            </li>
          ))}
        </ol>
        {installation.key === "install_suspect" && <p className="ui-muted">{t("agent.pipeline.suspect", { minutes: installation.grace_minutes })}</p>}
      </section>

      <div className="ui-two-columns">
        <section className="ui-panel">
          <h2>{t("agent.transport.title")}</h2>
          <dl className="ui-dl">
            <Field label={t("agent.transport.agent")} value={(heartbeat.transport || "—").toUpperCase()} />
            <Field label={t("agent.transport.enrollment")} value={heartbeat.enrollment_transport} />
            <Field label={t("agent.transport.heartbeat")} value={heartbeat.heartbeat_transport} />
            <Field label={t("agent.transport.sourceIp")} value={heartbeat.source_ip} mono />
            <Field label={t("agent.transport.routeros")} value={data.firmware} />
          </dl>
          <p className="ui-muted">{heartbeat.transport === "legacy" ? t("agent.transport.legacyNote") : heartbeat.transport === "modern" ? t("agent.transport.modernNote") : ""}</p>
        </section>
        <section className="ui-panel">
          <h2>{t("agent.capabilities.title")}</h2>
          <ul className="ui-cap-list">
            {data.capabilities.map((cap) => (
              <li key={cap.key}>
                <div>
                  <strong>{t(`agent.capabilities.keys.${cap.key}`, { defaultValue: cap.label })}</strong>
                  {cap.detail && <small className="ui-muted">{cap.detail}</small>}
                </div>
                <span className={cap.available ? "ui-severity" : "ui-severity ui-severity-warning"}>{cap.available ? t("agent.ok") : t("agent.unavailable")}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <section className="ui-panel">
        <div className="ui-panel-header">
          <div>
            <h2>{t("agent.credential.title")}</h2>
            <p className="ui-muted">{t("agent.credential.hint")}</p>
          </div>
          {data.actions.reinstall && !reinstall.data && (
            <button type="button" className="ui-button ui-button-ghost" disabled={reinstall.isPending} onClick={() => reinstall.mutate()}>
              {t("agent.credential.reinstall")}
            </button>
          )}
        </div>
        {reinstall.data && <EnrollmentCommand command={reinstall.data.enrollment_command} commandV6={reinstall.data.enrollment_command_v6} />}
        {reinstall.error && <div className="ui-alert ui-alert-danger">{(reinstall.error as Error).message}</div>}
        <dl className="ui-dl">
          <Field label={t("agent.credential.state")} value={credential ? (credential.active ? t("agent.credential.active") : t("agent.credential.inactive")) : t("agent.credential.none")} />
          <Field label={t("agent.credential.created")} value={formatDateTime(credential?.created_at, i18n.language)} />
          <Field label={t("agent.credential.lastUsed")} value={formatDateTime(credential?.last_used_at, i18n.language)} />
          <Field label={t("agent.credential.rotated")} value={formatDateTime(credential?.rotated_at, i18n.language)} />
        </dl>
      </section>

      <section className="ui-panel">
        <h2>{t("agent.enrollments.title")}</h2>
        {data.enrollments.length ? (
          <div className="ui-log-wrap ui-log-wrap-short">
            <table className="ui-log-table">
              <thead>
                <tr>
                  <th>{t("agent.enrollments.created")}</th>
                  <th>{t("agent.enrollments.source")}</th>
                  <th>{t("agent.enrollments.status")}</th>
                  <th>{t("agent.enrollments.expires")}</th>
                  <th>{t("agent.enrollments.used")}</th>
                </tr>
              </thead>
              <tbody>
                {data.enrollments.map((row, index) => (
                  <tr key={`${row.created_at}-${index}`}>
                    <td className="ui-nowrap">{formatDateTime(row.created_at, i18n.language)}</td>
                    <td>{eventSource(t, row.source)}</td>
                    <td><span className="ui-severity">{t(`agent.enrollments.statuses.${row.status}`, { defaultValue: row.status })}</span></td>
                    <td className="ui-nowrap">{formatDateTime(row.expires_at, i18n.language)}</td>
                    <td className="ui-nowrap">{formatDateTime(row.used_at, i18n.language)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="ui-muted">{t("agent.enrollments.empty")}</p>
        )}
      </section>
    </div>
  );
}

function UpdatePanel({ id, data }: { id: string; data: AgentStatus }) {
  const { t, i18n } = useTranslation();
  const mutation = useAgentUpdate(id);
  const { update } = data;
  return (
    <section className="ui-panel">
      <div className="ui-panel-header">
        <div>
          <h2>{t("agent.update.title")}</h2>
          <p className="ui-muted">{t("agent.update.hint")}</p>
        </div>
        {data.actions.update && (
          <button type="button" className="ui-button ui-button-primary" disabled={mutation.isPending} onClick={() => mutation.mutate()}>
            {t("agent.update.now", { version: update.target_version })}
          </button>
        )}
      </div>
      <dl className="ui-dl">
        <Field label={t("agent.update.installed")} value={update.current_version} />
        <Field label={t("agent.update.latest")} value={update.target_version} />
        <Field label={t("agent.update.status")} value={update.outdated ? t("agent.update.outdated") : t("agent.update.current")} />
        <Field label={t("agent.update.mode")} value={update.self_update_capable ? t("agent.update.automatic") : t("agent.update.reinstallOnce")} />
        <Field
          label={t("agent.update.last")}
          value={`${t(`agent.update.states.${update.state}`, { defaultValue: update.state })}${update.verified_at && update.state === "verified" ? ` · ${formatDateTime(update.verified_at, i18n.language)}` : ""}`}
        />
        <Field label={t("agent.update.policies")} value={update.policies.length ? update.policies.join(", ") : t("agent.update.noPolicies")} />
      </dl>
      {update.source_drift && <div className="ui-alert ui-alert-warning">{t("agent.update.drift")}</div>}
      {update.transport_upgrade && <p className="ui-muted">{update.self_update_capable ? t("agent.update.toModern") : t("agent.update.toModernReinstall")}</p>}
      {update.outdated && !update.self_update_capable && (
        <div className="ui-alert ui-alert-warning">
          <strong>{t("agent.update.reinstallTitle")}</strong> {update.reinstall_reason ?? t("agent.update.reinstallDefault")} {t("agent.update.reinstallHow")}
        </div>
      )}
      {mutation.isSuccess && <div className="ui-alert ui-alert-success">{t("agent.update.queued")}</div>}
      {mutation.error && <div className="ui-alert ui-alert-danger">{(mutation.error as Error).message}</div>}
    </section>
  );
}

function Field({ label, value, mono }: { label: string; value: string | null | undefined; mono?: boolean }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd className={mono ? "ui-mono" : undefined}>{value || "—"}</dd>
    </div>
  );
}
