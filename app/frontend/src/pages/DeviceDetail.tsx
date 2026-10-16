import { Link, Outlet, useParams } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import { BrandIcon } from "../app/BrandIcon";
import { type DeviceDetail as Detail, useDevice, useZabbixExclude } from "../api/device";
import { formatRelative } from "../lib/format";
import { DeviceZabbixPanel, ZabbixSeverity } from "../app/ZabbixProblems";

/** Device tabs and their routes under /devices/$deviceId. */
const MIGRATED_TABS = {
  overview: "/devices/$deviceId",
  monitor: "/devices/$deviceId/monitor",
  logs: "/devices/$deviceId/syslog",
  backups: "/devices/$deviceId/backups",
  agent: "/devices/$deviceId/agent",
  diagnostics: "/devices/$deviceId/diagnostics",
  exposure: "/devices/$deviceId/exposure",
  compliance: "/devices/$deviceId/compliance",
  activity: "/devices/$deviceId/jobs",
  configuration: "/devices/$deviceId/configuration",
  firmware: "/devices/$deviceId/firmware-upgrade",
  uisp: "/devices/$deviceId/uisp",
  cnmaestro: "/devices/$deviceId/cnmaestro",
  acs: "/devices/$deviceId/genieacs",
} as const;

/** Device shell: breadcrumbs, title, actions and tabs; the active tab renders in the outlet. */
export function DeviceDetail() {
  const { t } = useTranslation();
  const { deviceId } = useParams({ from: "/devices/$deviceId" });
  const query = useDevice(deviceId);

  if (query.isPending) return <div className="ui-empty">{t("app.loading")}</div>;
  if (query.isError) {
    const status = query.error instanceof ApiError ? query.error.status : 0;
    return <div className="ui-empty">{status === 404 ? t("device.notFound") : t("app.error")}</div>;
  }
  return <DeviceShell detail={query.data} />;
}

function DeviceShell({ detail }: { detail: Detail }) {
  const { t } = useTranslation();
  const { device } = detail;
  return (
    <section className="ui-page ui-device">
      <nav className="ui-breadcrumbs" aria-label={t("device.breadcrumbs")}>
        <Link to="/devices">{t("nav.devices")}</Link>
        {device.customer && (
          <>
            <span aria-hidden="true">›</span>
            <Link to="/customers/$customerId" params={{ customerId: device.customer.id }}>{device.customer.name}</Link>
          </>
        )}
        {device.site && (
          <>
            <span aria-hidden="true">›</span>
            <span>{device.site.name}</span>
          </>
        )}
      </nav>

      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1 className="ui-with-brand-title">
            <BrandIcon brand={device.brand} />
            {device.name} <span className={`ui-status ui-status-${device.status}`}>{t(`devices.statusLabel.${device.status}`, { defaultValue: device.status })}</span>
          </h1>
          <p>
            {[device.vendor, device.model, device.device_type].filter(Boolean).join(" · ")}
            {detail.zabbix?.problems?.items.length ? (
              <>
                {" "}
                <Link to="/devices/$deviceId" params={{ deviceId: device.id }} hash="zabbix-problems">
                  <ZabbixSeverity severity={detail.zabbix.problems.items[0].severity} /> {t("zabbixProblems.count", { count: detail.zabbix.problems.items.length })}
                </Link>
              </>
            ) : null}
          </p>
        </div>
        <div className="ui-actions">
          {detail.actions.reboot && (
            <Link className="ui-button ui-button-ghost" to="/devices/$deviceId/reboot" params={{ deviceId: device.id }}>
              {t("device.actions.reboot")}
            </Link>
          )}
          {detail.actions.incident && (
            <Link className="ui-button ui-button-ghost" to="/incidents/new" search={{ device: device.id }}>
              {t("device.actions.incident")}
            </Link>
          )}
          {detail.actions.manage && (
            <Link className="ui-button ui-button-ghost" to="/devices/$deviceId/manage" params={{ deviceId: device.id }}>
              {t("device.actions.manage")}
            </Link>
          )}
        </div>
      </header>

      <nav className="ui-tabs" aria-label={t("device.tabs.label")}>
        {detail.tabs.map((tab) =>
          tab.key in MIGRATED_TABS ? (
            <Link
              key={tab.key}
              to={MIGRATED_TABS[tab.key as keyof typeof MIGRATED_TABS]}
              params={{ deviceId: device.id }}
              className="ui-tab"
              activeOptions={{ exact: tab.key === "overview" }}
              activeProps={{ className: "ui-tab is-active", "aria-current": "page" }}
            >
              {t(`device.tabs.${tab.key}`)}
            </Link>
          ) : null,
        )}
      </nav>
      <Outlet />
    </section>
  );
}

export function DeviceOverview() {
  const { deviceId } = useParams({ from: "/devices/$deviceId/" });
  const { data: detail } = useDevice(deviceId);
  const { t, i18n } = useTranslation();
  if (!detail) return null;
  const { device } = detail;
  const severe = (detail.vulnerabilities.critical ?? 0) + (detail.vulnerabilities.high ?? 0);
  const openVulns = Object.values(detail.vulnerabilities).reduce((sum, value) => sum + value, 0);
  return (
    <>
      <div className="ui-metrics">
        <Metric label={t("device.metrics.status")} value={t(`devices.statusLabel.${device.status}`, { defaultValue: device.status })}
          hint={device.last_seen ? t("device.seen", { when: formatRelative(device.last_seen, i18n.language) }) : t("device.neverSeen")} tone={device.status === "offline" ? "danger" : undefined} />
        <Metric label={t("device.metrics.firmware")} value={device.firmware ?? "—"}
          hint={device.recommended_firmware && device.recommended_firmware !== device.firmware
            ? t("device.recommended", { version: device.recommended_firmware })
            : t(`device.firmwareStatus.${device.firmware_status ?? "unknown"}`, { defaultValue: "" })}
          tone={device.firmware_status === "security_update" ? "danger" : device.firmware_status === "update_available" ? "warning" : undefined} />
        <Metric label={t("device.metrics.vulnerabilities")} value={String(openVulns)} hint={t("device.severe", { count: severe })} tone={severe ? "danger" : undefined} />
        <Metric label={t("device.metrics.backup")} value={detail.backup.method}
          hint={detail.backup.last_success ? t("device.lastBackup", { when: formatRelative(detail.backup.last_success, i18n.language) }) : t("device.noBackup")}
          tone={detail.backup.executable ? undefined : "warning"} />
        <Metric label={t("device.metrics.ping")}
          value={!detail.icmp.enabled ? t("device.pingOff") : detail.icmp.down ? t("device.pingDown") : detail.icmp.last_rtt != null ? `${detail.icmp.last_rtt} ms` : t("device.pingWaiting")}
          hint={detail.icmp.enabled && detail.icmp.last_loss != null ? t("device.loss", { value: detail.icmp.last_loss }) : (detail.icmp.target ?? "")}
          tone={detail.icmp.down ? "danger" : undefined} />
      </div>

      <div className="ui-two-columns">
        <section className="ui-panel">
          <h2>{t("device.identity.title")}</h2>
          <dl className="ui-dl">
            <Field label={t("device.identity.identity")} value={device.identity} />
            <Field label={t("device.identity.managementIp")} value={device.management_ip} mono />
            <Field label={t("device.identity.lanIp")} value={device.lan_ip} mono />
            <Field label={t("device.identity.seenFrom")} value={device.seen_from !== device.management_ip ? device.seen_from : null} mono />
            <Field label={t("device.identity.mac")} value={device.primary_mac} mono />
            <Field label={t("device.identity.serial")} value={device.serial_number} mono />
            <Field label={t("device.identity.architecture")} value={device.architecture} />
            <Field label={t("device.identity.routerboot")} value={device.routerboot} />
            <Field label={t("device.identity.uptime")} value={device.uptime} />
            <Field label={t("device.identity.source")} value={device.management_source} />
            <Field label={t("device.identity.lifecycle")} value="—">
              <Link to="/devices/$deviceId/lifecycle" params={{ deviceId: device.id }}>
                {device.lifecycle_status && device.lifecycle_status !== "unknown" ? [device.lifecycle_status.toUpperCase(), device.eos_date].filter(Boolean).join(" · ") : t("device.lifecycleManage")}
              </Link>
            </Field>
            {detail.zabbix && <ZabbixField deviceId={device.id} zabbix={detail.zabbix} canEdit={detail.actions.manage} />}
            {detail.agent && (
              <Field label={t("device.identity.agent")} value={[detail.agent.version, detail.agent.transport].filter(Boolean).join(" · ") || t("device.agentMissing")}>
                <Link to="/devices/$deviceId/agent" params={{ deviceId: device.id }}>{[detail.agent.version, detail.agent.transport].filter(Boolean).join(" · ") || t("device.agentMissing")}</Link>
              </Field>
            )}
          </dl>
        </section>

        <section className="ui-panel">
          <h2>{t("device.issues.title")}</h2>
          {detail.issues.length === 0 ? (
            <p className="ui-muted">{t("device.issues.none")}</p>
          ) : (
            <ul className="ui-list">
              {detail.issues.map((issue) => (
                <li key={issue.id}>
                  <span className={`ui-severity ui-severity-${issue.severity}`}>{issue.severity}</span>
                  <Link to="/action-center" search={{ device: device.id }}>{issue.title}</Link>
                  <small>{formatRelative(issue.created_at, i18n.language)}</small>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
      {detail.zabbix && <DeviceZabbixPanel zabbix={detail.zabbix} />}
    </>
  );
}

function ZabbixField({ deviceId, zabbix, canEdit }: { deviceId: string; zabbix: NonNullable<Detail["zabbix"]>; canEdit: boolean }) {
  const { t } = useTranslation();
  const toggle = useZabbixExclude(deviceId);
  const state = zabbix.excluded ? t("zabbixProblems.excluded") : zabbix.link ? t("zabbixProblems.open") : t("zabbixProblems.pending");
  return (
    <Field label="Zabbix" value={state}>
      {zabbix.link ? <a href={zabbix.link} target="_blank" rel="noopener noreferrer">{t("zabbixProblems.open")}</a> : state}
      {canEdit && (
        <>
          {" · "}
          <button type="button" className="ui-link-button" disabled={toggle.isPending} onClick={() => toggle.mutate(!zabbix.excluded)}>
            {zabbix.excluded ? t("zabbixProblems.include") : t("zabbixProblems.exclude")}
          </button>
        </>
      )}
      {toggle.error && <small className="ui-text-danger ui-block">{(toggle.error as Error).message}</small>}
    </Field>
  );
}

export function Metric({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "danger" | "warning" }) {
  return (
    <article className={`ui-metric${tone ? ` ui-metric-${tone}` : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {hint && <small>{hint}</small>}
    </article>
  );
}

function Field({ label, value, mono, children }: { label: string; value: string | null | undefined; mono?: boolean; children?: ReactNode }) {
  if (!value) return null;
  return (
    <div>
      <dt>{label}</dt>
      <dd className={mono ? "ui-mono" : undefined}>{children ?? value}</dd>
    </div>
  );
}
