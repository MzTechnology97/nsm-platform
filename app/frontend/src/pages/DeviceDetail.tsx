import { Link, Outlet, useParams } from "@tanstack/react-router";
import { type FormEvent, type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import { BrandIcon } from "../app/BrandIcon";
import { type DeviceDetail as Detail, useDecommission, useDevice, useZabbixExclude, type DeviceIspbillingAccount } from "../api/device";
import { formatDateTime, formatDuration, formatRelative } from "../lib/format";
import { InfoTip } from "../app/InfoTip";
import { Resources } from "../app/Resources";
import { DeviceZabbixPanel, ZabbixSeverity } from "../app/ZabbixProblems";
import { Loading } from "../app/States";
import { eventSource } from "../lib/events";
import { PingToggleDialog } from "../app/PingToggle";
import { RingTile, type TileTone } from "../app/RingTile";

/** Device tabs and their routes under /devices/$deviceId. */
// Backup methods NSM cannot run yet (TR-069 CPEs, unknown vendors): "never" would read as a failure.
const NO_BACKUP_METHOD = new Set(["acs_required", "unsupported"]);

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

  if (query.isPending) return <Loading />;
  if (query.isError) {
    const status = query.error instanceof ApiError ? query.error.status : 0;
    return <div className="ui-empty">{status === 404 ? t("device.notFound") : t("app.error")}</div>;
  }
  return <DeviceShell detail={query.data} />;
}

function DeviceShell({ detail }: { detail: Detail }) {
  const [decommissionOpen, setDecommissionOpen] = useState(false);
  const { t, i18n } = useTranslation();
  const { device } = detail;
  const severe = (detail.vulnerabilities.critical ?? 0) + (detail.vulnerabilities.high ?? 0);
  const openVulns = Object.values(detail.vulnerabilities).reduce((sum, value) => sum + value, 0);
  const uptime = device.telemetry?.uptime_seconds;
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

      <header className="ui-device-head">
        <span className="ui-device-head-icon"><BrandIcon brand={device.brand} size={30} /></span>
        <div className="ui-device-head-main">
          <div className="ui-device-head-title">
            <h1>{device.name}</h1>
            {detail.decommissioned ? (
              <span className="ui-status ui-status-decommissioned">{t("decommission.badge")}</span>
            ) : (
              <span className={`ui-status ui-status-${device.status}`}>{t(`devices.statusLabel.${device.status}`, { defaultValue: device.status })}</span>
            )}
            {openVulns > 0 && (
              <Link to="/vulnerabilities" search={{ device: device.id } as never} className={`ui-status ui-status-${severe ? "offline" : "warning"} ui-device-head-cves`}>
                {t("device.head.cves", { count: openVulns })}
              </Link>
            )}
          </div>
          <p className="ui-device-head-facts">
            <span>{[device.vendor === "generic" ? t("devices.genericVendor") : device.brand?.known ? device.brand.label : device.vendor, device.model, device.device_type].filter(Boolean).join(" · ")}</span>
            {device.firmware && <span>{device.firmware}</span>}
            {device.management_ip && <span className="ui-mono">{device.management_ip}</span>}
            {device.status === "online" && uptime != null ? (
              <span>{t("devices.uptimeShort", { value: formatDuration(uptime, i18n.language) })}</span>
            ) : (
              <span>{device.last_seen ? t("device.seen", { when: formatRelative(device.last_seen, i18n.language) }) : t("device.neverSeen")}</span>
            )}
            {detail.zabbix?.problems?.items.length ? (
              <Link to="/devices/$deviceId" params={{ deviceId: device.id }} hash="zabbix-problems">
                <ZabbixSeverity severity={detail.zabbix.problems.items[0].severity} /> {t("zabbixProblems.count", { count: detail.zabbix.problems.items.length })}
              </Link>
            ) : null}
          </p>
        </div>
        <div className="ui-actions ui-device-head-actions">
          {!detail.decommissioned && detail.actions.reboot && (
            <Link className="ui-button ui-button-ghost" to="/devices/$deviceId/reboot" params={{ deviceId: device.id }}>
              {t("device.actions.reboot")}
            </Link>
          )}
          {!detail.decommissioned && detail.actions.incident && (
            <Link className="ui-button ui-button-ghost" to="/incidents/new" search={{ device: device.id }}>
              {t("device.actions.incident")}
            </Link>
          )}
          {!detail.decommissioned && detail.actions.manage && (
            <Link className="ui-button ui-button-primary" to="/devices/$deviceId/manage" params={{ deviceId: device.id }}>
              {t("device.actions.manage")}
            </Link>
          )}
          {!detail.decommissioned && detail.actions.decommission && !decommissionOpen && (
            <button type="button" className="ui-button ui-button-ghost ui-button-quiet-danger" onClick={() => setDecommissionOpen(true)}>
              {t("decommission.open")}
            </button>
          )}
        </div>
      </header>
      <DecommissionPanel detail={detail} open={decommissionOpen} onClose={() => setDecommissionOpen(false)} />

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

/** Out-of-service banner with reactivation, or (opened from the header action) the decommission form with its required reason. */
function DecommissionPanel({ detail, open, onClose }: { detail: Detail; open: boolean; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const { decommission, reactivate } = useDecommission(detail.device.id);
  const [reason, setReason] = useState("");
  const state = detail.decommissioned;
  const error = (decommission.error ?? reactivate.error) as Error | null;

  if (state) {
    return (
      <div className="ui-alert ui-alert-warning ui-decommission" role="status">
        <div>
          <strong>{t("decommission.since", { when: formatDateTime(state.at, i18n.language), who: state.by ?? "—" })}</strong>
          {state.reason && <span className="ui-block">{t("decommission.reasonLabel")}: {state.reason}</span>}
          <small className="ui-block ui-muted">{t("decommission.effects")}</small>
          {error && <small className="ui-block ui-backup-error">{error.message}</small>}
        </div>
        {detail.actions.decommission && (
          <button type="button" className="ui-button" disabled={reactivate.isPending} onClick={() => reactivate.mutate()}>
            {t("decommission.reactivate")}
          </button>
        )}
      </div>
    );
  }
  if (!detail.actions.decommission || !open) return null;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    decommission.mutate(reason.trim(), { onSuccess: () => { onClose(); setReason(""); } });
  };
  return (
    <form className="ui-panel ui-admin-form ui-admin-stack ui-danger-zone ui-decommission" onSubmit={submit}>
      <h2>{t("decommission.title")}</h2>
      <p className="ui-muted">{t("decommission.explain")}</p>
      <label><span>{t("decommission.reasonLabel")}</span>
        <input value={reason} onChange={(event) => setReason(event.target.value)} minLength={5} maxLength={500} required autoComplete="off"
          placeholder={t("decommission.reasonPlaceholder")} /></label>
      <div className="ui-row-actions">
        <button type="submit" className="ui-button ui-button-danger" disabled={decommission.isPending || reason.trim().length < 5}>{t("decommission.submit")}</button>
        <button type="button" className="ui-button ui-button-ghost" onClick={onClose}>{t("customerForm.cancel")}</button>
      </div>
      {error && <small className="ui-backup-error">{error.message}</small>}
    </form>
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
  const online = device.status === "online";
  const cpu = device.telemetry?.cpu ?? null;
  const ram = device.telemetry?.ram ?? null;
  const load = (value: number): TileTone => (value >= 90 ? "danger" : value >= 75 ? "warning" : "info");
  const loadNote = online ? t("device.health.resources") : t("device.health.lastValues");
  const firmwareTone: TileTone | undefined = device.firmware_status === "security_update" ? "danger"
    : device.firmware_status === "update_available" || (device.recommended_firmware && device.recommended_firmware !== device.firmware) ? "warning"
      : device.firmware_status === "current" ? "success" : undefined;
  const [pingOpen, setPingOpen] = useState(false);
  const loss = detail.icmp.enabled ? detail.icmp.last_loss : null;
  return (
    <>
      <div className="ui-metrics ui-ring-tiles">
        {cpu != null && (
          <RingTile label={<>{t("devices.cpu")} <InfoTip text={t("device.health.resourcesTip")} /></>} value={`${Math.round(cpu)}%`} percent={cpu}
            tone={load(cpu)} note={loadNote} />
        )}
        {ram != null && <RingTile label={t("devices.ram")} value={`${Math.round(ram)}%`} percent={ram} tone={load(ram)} note={loadNote} />}
        <RingTile label={t("device.metrics.ping")}
          value={!detail.icmp.enabled ? t("device.pingOff") : detail.icmp.down ? t("device.pingDown") : detail.icmp.last_rtt != null ? `${detail.icmp.last_rtt} ms` : t("device.pingWaiting")}
          note={loss != null ? t("device.loss", { value: loss }) : (detail.icmp.target ?? undefined)}
          percent={!detail.icmp.enabled ? null : detail.icmp.down ? 0 : loss != null ? 100 - loss : null}
          tone={detail.icmp.down ? "danger" : detail.icmp.enabled && detail.icmp.last_rtt != null ? "success" : undefined}
          onClick={() => setPingOpen(true)} actionLabel={t("device.pingToggle.open")} />
        <AvailabilityTile detail={detail} />
        <RingTile label={t("device.metrics.vulnerabilities")} value={String(openVulns)} note={t("device.severe", { count: severe })}
          percent={openVulns ? Math.max(8, (100 * severe) / openVulns) : 0} tone={severe ? "danger" : openVulns ? "warning" : "success"} />
        <RingTile label={t("device.metrics.firmware")} value={device.firmware ?? "—"}
          note={device.recommended_firmware && device.recommended_firmware !== device.firmware
            ? t("device.recommended", { version: device.recommended_firmware })
            : t(`device.firmwareStatus.${device.firmware_status ?? "unknown"}`, { defaultValue: "" })}
          percent={firmwareTone === "success" ? 100 : firmwareTone ? 50 : null} tone={firmwareTone} />
        <RingTile label={t("device.metrics.backup")}
          value={detail.backup.last_success ? formatRelative(detail.backup.last_success, i18n.language)
            : NO_BACKUP_METHOD.has(detail.backup.status) ? t("device.backupNotAvailable") : t("device.backupNever")}
          note={detail.backup.method}
          percent={detail.backup.last_success ? 100 : 0}
          tone={detail.backup.last_success ? "success" : NO_BACKUP_METHOD.has(detail.backup.status) ? undefined : !detail.backup.executable ? "warning" : undefined} />
      </div>
      {pingOpen && <PingToggleDialog deviceId={detail.device.id} onClose={() => setPingOpen(false)} />}

      <DeviceHealth detail={detail} showResources={cpu == null && ram == null} />

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
            <Field label={t("device.identity.source")} value={eventSource(t, device.management_source)} />
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
            <ul className="ui-list ui-issue-list">
              {detail.issues.map((issue) => (
                <li key={issue.id}>
                  <span className={`ui-severity ui-severity-${issue.severity}`}>{t(`actionCenter.severities.${issue.severity}`, { defaultValue: issue.severity })}</span>
                  <Link to="/action-center" search={{ device: device.id }}>{issue.title}</Link>
                  <small>{formatRelative(issue.created_at, i18n.language)}</small>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
      {detail.ispbilling?.length ? <IspbillingAccounts accounts={detail.ispbilling} /> : null}
      {detail.zabbix && <DeviceZabbixPanel zabbix={detail.zabbix} />}
    </>
  );
}

const RADIUS_TONE: Record<string, string> = { attivo: "online", sospeso: "warning", terminato: "offline" };

/** The ISPBilling RADIUS accounts of this device: who is billed for it and whether the account is active. */
function IspbillingAccounts({ accounts }: { accounts: DeviceIspbillingAccount[] }) {
  const { t } = useTranslation();
  return (
    <section className="ui-panel">
      <h2>{t("device.ispbilling.title")}</h2>
      <p className="ui-muted">{t("device.ispbilling.help")}</p>
      <ul className="ui-list">
        {accounts.map((account) => (
          <li key={account.username}>
            <span>
              <strong className="ui-mono">{account.username}</strong>
              <small className="ui-muted ui-block">
                {[account.profile, account.customer, t(`device.ispbilling.matched.${account.matched_by ?? "pppoe"}`)].filter(Boolean).join(" · ")}
              </small>
              {account.same_customer === false && <small className="ui-key-hint ui-block">{t("device.ispbilling.otherCustomer")}</small>}
            </span>
            <span />
            <span className={`ui-status ui-status-${RADIUS_TONE[(account.status ?? "").toLowerCase()] ?? "unknown"}`}>{account.status ?? "—"}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

const SEVERITIES = ["critical", "high", "medium", "low", "unknown"] as const;

/** Current load (CPU, memory, uptime) and the open CVEs by severity, each opening the filtered vulnerability list. */
function DeviceHealth({ detail, showResources }: { detail: Detail; showResources: boolean }) {
  const { t, i18n } = useTranslation();
  const { device } = detail;
  const online = device.status === "online";
  const uptime = device.telemetry?.uptime_seconds;
  // Advisories without a known severity (empty, "none", null) count as "unknown".
  const counts: Record<string, number> = {};
  for (const [level, count] of Object.entries(detail.vulnerabilities)) {
    const key = (SEVERITIES as readonly string[]).includes(level) ? level : "unknown";
    counts[key] = (counts[key] ?? 0) + count;
  }
  const open = SEVERITIES.filter((level) => (counts[level] ?? 0) > 0);
  const evaluated = device.cves?.evaluated ?? true;
  return (
    <section className="ui-panel ui-device-health">
      {showResources && <div className="ui-device-health-part">
        <h3>{t("device.health.resources")} <InfoTip text={t("device.health.resourcesTip")} /></h3>
        <Resources telemetry={device.telemetry} online={online} />
        {uptime != null && (
          <small className="ui-muted">{online ? t("device.health.uptime", { value: formatDuration(uptime, i18n.language) }) : t("device.health.lastValues")}</small>
        )}
      </div>}
      <div className="ui-device-health-part">
        <h3>{t("device.health.cves")} <InfoTip text={t("device.health.cvesTip")} /></h3>
        {open.length ? (
          <div className="ui-cve-chips">
            {open.map((level) => (
              <Link key={level} to="/vulnerabilities" search={{ device: device.id, severity: level } as never} className={`ui-cve-chip is-${level}`}>
                <b>{counts[level]}</b> {t(`vulns.levels.${level}`)}
              </Link>
            ))}
            <Link to="/vulnerabilities" search={{ device: device.id } as never} className="ui-cve-chip-all">{t("device.health.allCves")}</Link>
          </div>
        ) : (
          <span className={`ui-cve-badge ${evaluated ? "is-clean" : "is-unknown"}`}>
            {evaluated ? t("device.health.noCves") : t("device.health.notEvaluable")}
          </span>
        )}
      </div>
    </section>
  );
}

/** Ping availability of the last 30 days (maintenance excluded), green from the customer's SLA target (or 99%), amber within one point below. */
function AvailabilityTile({ detail }: { detail: Detail }) {
  const { t, i18n } = useTranslation();
  const data = detail.availability_30d;
  if (!data) return null;
  const label = <>{t("device.availability.label")} <InfoTip text={t("device.availability.tip", { target: format(data.target, i18n.language) })} /></>;
  if (!data.measured || data.availability == null) {
    return <RingTile label={label} value={t("device.availability.notMeasured")} note={t("device.availability.notMeasuredNote")} percent={null} />;
  }
  const tone: TileTone = data.availability >= data.target ? "success" : data.availability >= data.target - 1 ? "warning" : "danger";
  const downtime = data.downtime_minutes ?? 0;
  const note = downtime > 0
    ? t("device.availability.downtime", { value: minutesLabel(downtime, i18n.language), count: data.outages ?? 0 })
    : t("device.availability.none");
  return <RingTile label={label} value={`${format(data.availability, i18n.language)}%`} note={note} percent={data.availability} tone={tone} />;
}

const format = (value: number, locale: string) => new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(value);

function minutesLabel(value: number, locale: string): string {
  const unit = (amount: number, name: "minute" | "hour" | "day") =>
    new Intl.NumberFormat(locale, { style: "unit", unit: name, unitDisplay: "narrow", maximumFractionDigits: amount < 10 ? 1 : 0 }).format(amount);
  if (value < 120) return unit(value, "minute");
  if (value < 48 * 60) return unit(value / 60, "hour");
  return unit(value / 1440, "day");
}

function ZabbixField({ deviceId, zabbix, canEdit }: { deviceId: string; zabbix: NonNullable<Detail["zabbix"]>; canEdit: boolean }) {
  const { t } = useTranslation();
  const toggle = useZabbixExclude(deviceId);
  const state = zabbix.excluded ? t("zabbixProblems.excluded") : zabbix.link ? t("zabbixProblems.open") : t("zabbixProblems.pending");
  return (
    <Field label="Zabbix" value={state}>
      {zabbix.link ? <a href={zabbix.link} target="_blank" rel="noopener noreferrer">{t("zabbixProblems.open")}</a> : state}
      {zabbix.linked && <small className="ui-muted ui-block">{t(`zabbixProblems.linked.${zabbix.match === "name" ? "name" : "ip"}`)}</small>}
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
