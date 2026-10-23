import { Link, useParams } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import { useDevice } from "../api/device";
import { type FirmwarePlanTab, useFirmwareAction, useFirmwarePlan } from "../api/deviceTabs";
import { AcsFirmware } from "../app/AcsFirmware";
import { FirmwareSchedulePanel } from "../app/FirmwareSchedule";
import { formatDateTime } from "../lib/format";
import { Loading } from "../app/States";

type Plan = NonNullable<FirmwarePlanTab["plan"]>;
const STATUS_TONE: Record<string, string> = { success: "online", cancelled: "offline", failed: "offline", ready: "online", approved: "online", staged: "online" };
const STAGED = ["staged", "activation_pending", "reboot_pending", "success"];

function errorText(t: (key: string, options?: Record<string, unknown>) => string, error: unknown) {
  return error instanceof ApiError ? t(`firmwarePlan.errors.${error.code ?? "generic"}`, { defaultValue: error.message }) + (error.code && error.message ? ` ${error.message}` : "")
    : (error as Error | null)?.message;
}

/** One gated step: enabled only with the exact literal confirmation, checked again by the server. */
function Gate({ deviceId, path, expected, title, help, button, body }: {
  deviceId: string; path: string; expected: string; title: string; help: string; button: string; body?: Record<string, unknown>;
}) {
  const { t } = useTranslation();
  const run = useFirmwareAction<Record<string, unknown>>(deviceId, path);
  const [typed, setTyped] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    run.mutate({ ...body, confirmation: typed }, { onSuccess: () => setTyped("") });
  };
  return (
    <form className="ui-admin-form ui-admin-stack ui-danger-zone" onSubmit={submit}>
      <h3>{title}</h3>
      <p className="ui-muted">{help}</p>
      <label><span>{t("deviceRouterboot.confirm", { text: expected })}</span>
        <input value={typed} onChange={(event) => setTyped(event.target.value)} required autoComplete="off" className="ui-mono" /></label>
      <button type="submit" className="ui-button ui-button-danger" disabled={run.isPending || typed.trim() !== expected}>{button}</button>
      {run.error && <small className="ui-backup-error">{errorText(t, run.error)}</small>}
    </form>
  );
}

function PlanSteps({ plan }: { plan: Plan }) {
  const { t } = useTranslation();
  const steps = [
    { key: "readiness", state: "done" },
    { key: "backup", state: plan.backup_status === "success" ? "done" : plan.backup_status === "failed" ? "failed" : "pending" },
    { key: "approval", state: plan.approved_at ? "done" : "pending" },
    { key: "download", state: STAGED.includes(plan.status) ? "done" : plan.status === "staging" ? "running" : "pending" },
    { key: "activation", state: plan.status === "success" ? "done" : ["activation_pending", "reboot_pending"].includes(plan.status) ? "running" : "pending" },
  ];
  return (
    <ol className="ui-steps">
      {steps.map((step, index) => (
        <li key={step.key} className={`is-${step.state}`}>
          <span className="ui-step-mark">{step.state === "done" ? "✓" : step.state === "failed" ? "!" : index + 1}</span>
          <div>
            <strong>{t(`firmwarePlan.steps.${step.key}.title`)}</strong>
            <small className="ui-muted">{t(`firmwarePlan.steps.${step.key}.help`)}</small>
          </div>
          <span className={`ui-status ui-status-${step.state === "done" ? "online" : step.state === "failed" ? "offline" : "warning"}`}>
            {t(`deviceRouterboot.stepStates.${step.state}`)}
          </span>
        </li>
      ))}
    </ol>
  );
}

/** Firmware tab: the RouterOS upgrade plan for MikroTik, the ACS upgrade for TR-069 CPEs (ACS-08). */
export function DeviceFirmwareUpgrade() {
  const { deviceId } = useParams({ from: "/devices/$deviceId/firmware-upgrade" });
  const device = useDevice(deviceId);
  if (!device.data) return <Loading />;
  if (device.data.device.vendor !== "mikrotik" && device.data.tabs.some((tab) => tab.key === "acs")) return <AcsFirmware deviceId={deviceId} />;
  return <RouterosFirmwareUpgrade deviceId={deviceId} />;
}

/** RouterOS upgrade plan: pre-upgrade backup, approval, package download and activation as separate, confirmed and verified steps. */
function RouterosFirmwareUpgrade({ deviceId }: { deviceId: string }) {
  const { t, i18n } = useTranslation();
  const query = useFirmwarePlan(deviceId);
  const create = useFirmwareAction(deviceId, "firmware-upgrade/plan");
  const check = useFirmwareAction(deviceId, "firmware-upgrade/check");
  const data = query.data;
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <Loading />;
  const plan = data.plan;
  const active = plan && !["success", "cancelled", "failed"].includes(plan.status);
  const catalog = data.catalog;

  return (
    <>
      <section className="ui-panel ui-admin-section">
        <div className="ui-page-header-row">
          <div>
            <h2>{t("firmwarePlan.title")}</h2>
            <p className="ui-muted">{t("firmwarePlan.help")}</p>
          </div>
          <div className="ui-row-actions">
            <button type="button" className="ui-button ui-button-ghost" disabled={check.isPending || data.check_pending} onClick={() => check.mutate(undefined)}>
              {data.check_pending ? t("firmwarePlan.checking") : t("firmwarePlan.checkNow")}
            </button>
            <Link to="/devices/$deviceId/routerboot" params={{ deviceId }} className="ui-button ui-button-ghost">RouterBOOT</Link>
            <Link to="/firmware" className="ui-button ui-button-ghost">{t("firmwarePlan.worklist")}</Link>
          </div>
        </div>
        <div className="ui-metrics">
          <div className="ui-metric"><span>{t("firmwarePlan.installed")}</span><strong className="ui-mono">{data.installed ?? "—"}</strong></div>
          <div className="ui-metric"><span>{t("firmwarePlan.target")}</span><strong className="ui-mono">{data.readiness.latest_version ?? data.recommended ?? "—"}</strong>
            <small>{t("firmwarePlan.channel", { channel: data.readiness.channel ?? "—" })}</small></div>
          <div className="ui-metric"><span>{t("firmwarePlan.space")}</span><strong>{data.readiness.free_hdd_space ?? "—"}</strong></div>
          <div className="ui-metric"><span>{t("firmwarePlan.checked")}</span><strong>{formatDateTime(data.readiness.checked_at, i18n.language)}</strong>
            <small>{data.agent_transport ?? t("firmwarePlan.noTransport")}</small></div>
        </div>
        {catalog?.latest_version && (
          <p className={catalog.security ? "ui-alert ui-alert-warning" : "ui-muted"}>
            {t("firmwarePlan.catalog", { channel: catalog.channel, version: catalog.latest_version, when: formatDateTime(catalog.released_at, i18n.language) })}{" "}
            {catalog.newer ? (catalog.security ? t("firmwarePlan.catalogSecurity", { reasons: (catalog.security_reasons ?? []).join(", ") }) : t("firmwarePlan.catalogNewer"))
              : catalog.newer === false ? t("firmwarePlan.catalogCurrent") : ""}
          </p>
        )}
        <p className="ui-muted">{t("firmwarePlan.routerbootSeparate")}</p>
        {check.data?.state && check.data.state !== "queued" && check.data.state !== "already_queued" && (
          <small className="ui-backup-error ui-block">{t(`firmware.check.${check.data.state}`, { defaultValue: check.data.state })}</small>
        )}
        {check.error && <small className="ui-backup-error ui-block">{errorText(t, check.error)}</small>}
        {data.preparing && (data.preparing.error
          ? <div className="ui-alert ui-alert-warning">{t("firmwarePlan.prepareFailed", { error: data.preparing.error })}</div>
          : <div className="ui-alert">{t("firmwarePlan.preparing")}</div>)}
      </section>

      {!data.legacy && (data.schedule || data.can_execute) && (
        <FirmwareSchedulePanel deviceId={deviceId} schedule={data.schedule} canExecute={data.can_execute}
          target={data.readiness.latest_version ?? data.recommended} />
      )}

      {data.legacy && (
        <section className="ui-panel ui-admin-section">
          <h2>{t("firmwarePlan.legacyTitle")}</h2>
          <p className="ui-muted">{t("firmwarePlan.legacyHelp")}</p>
          {data.legacy.state.status && (
            <div className="ui-alert ui-alert-warning">
              {t(`firmwarePlan.legacyStates.${data.legacy.state.status}`, { defaultValue: data.legacy.state.status })} · {data.legacy.state.from_version ?? "—"} → {data.legacy.state.target_version ?? "—"}
              {data.legacy.state.last_error && <small className="ui-block">{data.legacy.state.last_error}</small>}
            </div>
          )}
          {data.legacy.blocker ? (
            <div className="ui-alert ui-alert-warning">{data.legacy.blocker}</div>
          ) : data.can_execute && data.legacy.confirmation ? (
            <LegacyUpgrade deviceId={deviceId} expected={data.legacy.confirmation} target={data.legacy.target ?? ""} />
          ) : null}
        </section>
      )}

      <section className="ui-panel ui-admin-section">
        {!plan ? (
          <>
            <h2>{t("firmwarePlan.noPlanTitle")}</h2>
            <p className="ui-muted">{t("firmwarePlan.noPlan")}</p>
            {data.can_execute && !data.legacy && (
              <button type="button" className="ui-button" disabled={create.isPending} onClick={() => create.mutate(undefined)}>{t("firmwarePlan.create")}</button>
            )}
            {create.error && <small className="ui-backup-error ui-block">{errorText(t, create.error)}</small>}
          </>
        ) : (
          <>
            <div className="ui-page-header-row">
              <div>
                <h2>{t("firmwarePlan.planTitle", { version: plan.target_version })}</h2>
                <p className="ui-muted">{plan.channel ? t("firmwarePlan.channel", { channel: plan.channel }) : null}</p>
              </div>
              <span className={`ui-status ui-status-${STATUS_TONE[plan.status] ?? "warning"}`}>{t(`firmwarePlan.states.${plan.status}`, { defaultValue: plan.status })}</span>
            </div>
            <dl className="ui-dl">
              {(["created_at", "ready_at", "approved_at", "started_at", "completed_at"] as const).map((key) => (
                <div key={key}><dt>{t(`firmwarePlan.dates.${key}`)}</dt><dd>{formatDateTime(plan[key], i18n.language)}</dd></div>
              ))}
              <div><dt>{t("firmwarePlan.backup")}</dt><dd>{plan.backup_status ? t(`firmwarePlan.backupStates.${plan.backup_status}`, { defaultValue: plan.backup_status }) : "—"}</dd></div>
            </dl>
            {plan.activation.accepted_at && !plan.activation.verified_at && <div className="ui-alert ui-alert-warning">{t("firmwarePlan.accepted")}</div>}
            {plan.activation.verified_at && <div className="ui-alert ui-alert-success">{t("firmwarePlan.verified", { version: plan.activation.observed_version ?? plan.target_version })}</div>}
            {plan.last_error && <div className="ui-alert ui-alert-danger">{plan.last_error}</div>}
            <PlanSteps plan={plan} />
            {data.can_execute && plan.status === "ready" && (
              <Gate deviceId={deviceId} path={`firmware-upgrade/${plan.id}/approve`} expected={plan.confirmations.approve}
                title={t("firmwarePlan.approveTitle")} help={t("firmwarePlan.approveHelp")} button={t("firmwarePlan.approve")} />
            )}
            {data.can_execute && plan.status === "approved" && (
              <Gate deviceId={deviceId} path={`firmware-upgrade/${plan.id}/stage`} expected={plan.confirmations.stage}
                title={t("firmwarePlan.stageTitle")} help={t("firmwarePlan.stageHelp")} button={t("firmwarePlan.stage")} />
            )}
            {data.can_execute && plan.status === "staged" && (
              <Gate deviceId={deviceId} path={`firmware-upgrade/${plan.id}/activate`} expected={plan.confirmations.activate}
                title={t("firmwarePlan.activateTitle")} help={t("firmwarePlan.activateHelp")} button={t("firmwarePlan.activate")} />
            )}
            {data.can_execute && active && !["reboot_pending"].includes(plan.status) && <CancelPlan deviceId={deviceId} planId={plan.id} />}
            {data.can_execute && !active && !data.legacy && (
              <button type="button" className="ui-button ui-button-ghost" disabled={create.isPending} onClick={() => create.mutate(undefined)}>{t("firmwarePlan.createNew")}</button>
            )}
            {create.error && <small className="ui-backup-error ui-block">{errorText(t, create.error)}</small>}
          </>
        )}
      </section>
    </>
  );
}

function CancelPlan({ deviceId, planId }: { deviceId: string; planId: string }) {
  const { t } = useTranslation();
  const cancel = useFirmwareAction(deviceId, `firmware-upgrade/${planId}/cancel`);
  const [confirming, setConfirming] = useState(false);
  return (
    <div className="ui-row-actions">
      {confirming ? (
        <>
          <span>{t("firmwarePlan.cancelConfirm")}</span>
          <button type="button" className="ui-button ui-button-danger ui-button-small" disabled={cancel.isPending} onClick={() => cancel.mutate(undefined)}>{t("firmwarePlan.cancel")}</button>
          <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={() => setConfirming(false)}>{t("customerForm.cancel")}</button>
        </>
      ) : (
        <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={() => setConfirming(true)}>{t("firmwarePlan.cancel")}</button>
      )}
      {cancel.error && <small className="ui-backup-error">{errorText(t, cancel.error)}</small>}
    </div>
  );
}

function LegacyUpgrade({ deviceId, expected, target }: { deviceId: string; expected: string; target: string }) {
  const { t } = useTranslation();
  const run = useFirmwareAction<{ confirmation: string; backup_ack: boolean }>(deviceId, "legacy-upgrade");
  const [typed, setTyped] = useState("");
  const [ack, setAck] = useState(false);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    run.mutate({ confirmation: typed, backup_ack: ack }, { onSuccess: () => { setTyped(""); setAck(false); } });
  };
  return (
    <form className="ui-admin-form ui-admin-stack ui-danger-zone" onSubmit={submit}>
      <p>{t("firmwarePlan.legacyTarget", { version: target })}</p>
      <label className="ui-with-brand"><input type="checkbox" checked={ack} onChange={(event) => setAck(event.target.checked)} /> <span>{t("firmwarePlan.legacyAck")}</span></label>
      <label><span>{t("deviceRouterboot.confirm", { text: expected })}</span>
        <input value={typed} onChange={(event) => setTyped(event.target.value)} required autoComplete="off" className="ui-mono" /></label>
      <button type="submit" className="ui-button ui-button-danger" disabled={run.isPending || !ack || typed.trim() !== expected}>{t("firmwarePlan.legacySubmit")}</button>
      {run.isSuccess && <small className="ui-live">{t("firmwarePlan.legacyQueued")}</small>}
      {run.error && <small className="ui-backup-error">{errorText(t, run.error)}</small>}
    </form>
  );
}
