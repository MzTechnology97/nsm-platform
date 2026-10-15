import { Link, useParams } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import { useDeviceReboot, useDeviceRouterboot, useOperation } from "../api/deviceTabs";
import { formatDateTime } from "../lib/format";

const JOB_TONE: Record<string, string> = { success: "online", completed: "online", failed: "offline", expired: "offline", pending: "warning", delivered: "warning", running: "warning" };
const STATE_TONE: Record<string, string> = { verified: "online", success: "online", failed: "offline", queued: "warning", rebooting: "warning", staging: "warning",
  staged: "warning", reboot_pending: "warning" };

function errorText(t: (key: string, options?: Record<string, unknown>) => string, error: unknown, prefix: string) {
  return error instanceof ApiError ? t(`${prefix}.${error.code ?? "generic"}`, { defaultValue: error.message }) : (error as Error | null)?.message;
}

/** Controlled reboot of a MikroTik: reason, confirmation with the device name, then the agent acknowledges and NSM verifies the uptime. */
export function DeviceReboot() {
  const { t, i18n } = useTranslation();
  const { deviceId } = useParams({ from: "/devices/$deviceId/reboot" });
  const query = useDeviceReboot(deviceId);
  const queue = useOperation<{ reason: string; confirm_name: string }>(deviceId, "reboot", "reboot");
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState("");
  const data = query.data;
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <div className="ui-empty">{t("app.loading")}</div>;
  const state = data.state;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    queue.mutate({ reason, confirm_name: typed }, { onSuccess: () => { setReason(""); setTyped(""); } });
  };
  return (
    <div className="ui-two-columns">
      <section className="ui-panel ui-admin-section">
        <h2>{t("deviceReboot.title")}</h2>
        <p className="ui-muted">{t("deviceReboot.help")}</p>
        {state.status && (
          <div className={`ui-alert ${state.status === "failed" ? "ui-alert-danger" : state.status === "verified" ? "ui-alert-success" : "ui-alert-warning"}`}>
            <strong>{t(`deviceReboot.states.${state.status}`, { defaultValue: state.status })}</strong>
            {state.last_error && <small className="ui-block">{state.last_error}</small>}
            <small className="ui-block">{t("deviceReboot.requested", { who: state.requested_by ?? "—", when: formatDateTime(state.queued_at, i18n.language) })}</small>
            {state.reason && <small className="ui-block">{t("deviceReboot.reason")}: {state.reason}</small>}
            {state.uptime_before && <small className="ui-block">{t("deviceReboot.uptime", { before: state.uptime_before, after: state.uptime_after ?? t("deviceReboot.waiting") })}</small>}
          </div>
        )}
        {data.blocker ? (
          <div className="ui-alert ui-alert-warning">{data.blocker}</div>
        ) : data.can_execute ? (
          <form className="ui-admin-form ui-admin-stack ui-danger-zone" onSubmit={submit}>
            <label><span>{t("deviceReboot.reason")}</span>
              <input value={reason} onChange={(event) => setReason(event.target.value)} minLength={5} maxLength={500} required autoComplete="off" /></label>
            <label><span>{t("customerForm.confirmName", { name: data.name })}</span>
              <input value={typed} onChange={(event) => setTyped(event.target.value)} required autoComplete="off" />
              <small className="ui-muted">{t("deviceReboot.impact")}</small></label>
            <button type="submit" className="ui-button ui-button-danger" disabled={queue.isPending || typed.trim() !== data.name || reason.trim().length < 5}>
              {t("deviceReboot.submit")}
            </button>
            {queue.isSuccess && <small className="ui-live">{t("deviceReboot.queued")}</small>}
            {queue.error && <small className="ui-backup-error">{errorText(t, queue.error, "deviceReboot.errors")}</small>}
          </form>
        ) : (
          <p className="ui-muted">{t("deviceReboot.noPermission")}</p>
        )}
        <Link to="/devices/$deviceId/routerboot" params={{ deviceId }}>{t("deviceReboot.routerboot")}</Link>
      </section>
      <section className="ui-panel ui-admin-section">
        <h2>{t("deviceReboot.history")}</h2>
        {data.history.length ? (
          <ul className="ui-list">
            {data.history.map((job) => (
              <li key={job.id}>
                <span>
                  <strong>{formatDateTime(job.created_at, i18n.language)}</strong>
                  <small className="ui-muted ui-block">{job.reason ?? "—"}</small>
                  {job.last_error && <small className="ui-backup-error ui-block">{job.last_error}</small>}
                </span>
                <span />
                <span className={`ui-status ui-status-${JOB_TONE[job.status] ?? "warning"}`}>{t(`deviceActivity.statuses.${job.status}`, { defaultValue: job.status })}</span>
              </li>
            ))}
          </ul>
        ) : (
          <div className="ui-empty">{t("deviceReboot.noHistory")}</div>
        )}
      </section>
    </div>
  );
}

function ConfirmedAction({ deviceId, action, expected, title, help, button }: {
  deviceId: string; action: "stage" | "reboot"; expected: string; title: string; help: string; button: string;
}) {
  const { t } = useTranslation();
  const run = useOperation<{ confirmation: string }>(deviceId, `routerboot/${action}`, "routerboot");
  const [typed, setTyped] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    run.mutate({ confirmation: typed }, { onSuccess: () => setTyped("") });
  };
  return (
    <form className="ui-admin-form ui-admin-stack ui-danger-zone" onSubmit={submit}>
      <h3>{title}</h3>
      <p className="ui-muted">{help}</p>
      <label><span>{t("deviceRouterboot.confirm", { text: expected })}</span>
        <input value={typed} onChange={(event) => setTyped(event.target.value)} required autoComplete="off" className="ui-mono" /></label>
      <button type="submit" className="ui-button ui-button-danger" disabled={run.isPending || typed.trim() !== expected}>{button}</button>
      {run.error && <small className="ui-backup-error">{errorText(t, run.error, "deviceRouterboot.errors")}</small>}
    </form>
  );
}

/** RouterBOOT lifecycle: bootloader flash and reboot as two separate, confirmed and verified steps. */
export function DeviceRouterboot() {
  const { t, i18n } = useTranslation();
  const { deviceId } = useParams({ from: "/devices/$deviceId/routerboot" });
  const query = useDeviceRouterboot(deviceId);
  const data = query.data;
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <div className="ui-empty">{t("app.loading")}</div>;
  const status = data.state.status ?? "idle";
  const busy = ["staging", "staged", "reboot_pending", "rebooting"].includes(status);
  const steps = [
    { key: "readiness", state: data.current && data.target ? "done" : "pending" },
    { key: "flash", state: ["staged", "reboot_pending", "rebooting", "success"].includes(status) ? "done" : status === "staging" ? "running" : "pending" },
    { key: "reboot", state: status === "success" ? "done" : ["reboot_pending", "rebooting"].includes(status) ? "running" : "pending" },
    { key: "verify", state: status === "success" ? "done" : status === "failed" ? "failed" : "pending" },
  ];
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <div>
          <h2>{t("deviceRouterboot.title")}</h2>
          <p className="ui-muted">{t("deviceRouterboot.help")}</p>
        </div>
        <Link to="/devices/$deviceId/reboot" params={{ deviceId }}>{t("deviceRouterboot.rebootPage")}</Link>
      </div>
      <div className="ui-metrics">
        <div className="ui-metric"><span>{t("deviceRouterboot.current")}</span><strong className="ui-mono">{data.current ?? "—"}</strong></div>
        <div className={`ui-metric${data.target && data.current !== data.target ? " ui-metric-warning" : ""}`}><span>{t("deviceRouterboot.available")}</span>
          <strong className="ui-mono">{data.target ?? "—"}</strong></div>
        <div className="ui-metric"><span>{t("deviceRouterboot.workflow")}</span>
          <strong><span className={`ui-status ui-status-${STATE_TONE[status] ?? "online"}`}>{t(`deviceRouterboot.states.${status}`, { defaultValue: status })}</span></strong></div>
        <div className="ui-metric"><span>{t("deviceRouterboot.checked")}</span><strong>{formatDateTime(data.checked_at, i18n.language)}</strong>
          <small>{[data.agent_transport, data.privilege_profile].filter(Boolean).join(" · ") || "—"}</small></div>
      </div>
      <p className="ui-muted">{t("deviceRouterboot.noAuto")}</p>
      {data.state.last_error && <div className="ui-alert ui-alert-danger">{data.state.last_error}</div>}
      {status === "success" && <div className="ui-alert ui-alert-success">{t("deviceRouterboot.verified", { version: data.state.observed_version ?? data.target })}</div>}
      <ol className="ui-steps">
        {steps.map((step, index) => (
          <li key={step.key} className={`is-${step.state}`}>
            <span className="ui-step-mark">{step.state === "done" ? "✓" : step.state === "failed" ? "!" : index + 1}</span>
            <div>
              <strong>{t(`deviceRouterboot.steps.${step.key}.title`)}</strong>
              <small className="ui-muted">{t(`deviceRouterboot.steps.${step.key}.help`)}</small>
            </div>
            <span className={`ui-status ui-status-${step.state === "done" ? "online" : step.state === "failed" ? "offline" : "warning"}`}>
              {t(`deviceRouterboot.stepStates.${step.state}`)}
            </span>
          </li>
        ))}
      </ol>
      {!data.current || !data.target ? (
        <div className="ui-alert ui-alert-warning">{t("deviceRouterboot.readFirst")}</div>
      ) : data.current === data.target && status !== "success" ? (
        <div className="ui-alert ui-alert-success">{t("deviceRouterboot.aligned", { version: data.current })}</div>
      ) : null}
      {data.can_execute && data.stage_confirmation && data.current && data.target && data.current !== data.target && !busy && (
        <ConfirmedAction deviceId={deviceId} action="stage" expected={data.stage_confirmation} title={t("deviceRouterboot.flashTitle", { version: data.target })}
          help={t("deviceRouterboot.flashHelp")} button={t("deviceRouterboot.flash")} />
      )}
      {status === "staging" && <div className="ui-alert ui-alert-warning">{t("deviceRouterboot.staging")}</div>}
      {status === "staged" && data.can_execute && data.reboot_confirmation && (
        <ConfirmedAction deviceId={deviceId} action="reboot" expected={data.reboot_confirmation} title={t("deviceRouterboot.rebootTitle")}
          help={t("deviceRouterboot.rebootHelp")} button={t("deviceRouterboot.reboot")} />
      )}
      {status === "reboot_pending" && <div className="ui-alert ui-alert-warning">{t("deviceRouterboot.rebootPending")}</div>}
      {status === "rebooting" && <div className="ui-alert ui-alert-warning">{t("deviceRouterboot.rebooting")}</div>}
    </section>
  );
}
