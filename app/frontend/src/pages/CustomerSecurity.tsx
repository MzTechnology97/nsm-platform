import { Link, useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { type CustomerSecurity as Data, type SecurityDevice, useCustomerSecurity } from "../api/customer";
import { BrandIcon } from "../app/BrandIcon";
import { formatDateTime } from "../lib/format";

const formatDate = (value: string, locale: string) => new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(new Date(value));

const SEVERITY_TONE: Record<string, string> = { critical: "ui-severity ui-severity-critical", high: "ui-severity ui-severity-high", medium: "ui-severity ui-severity-medium" };

function DeviceCell({ device }: { device: SecurityDevice }) {
  return (
    <span className="ui-with-brand">
      <BrandIcon brand={device.brand} small />
      <Link to="/devices/$deviceId" params={{ deviceId: device.id }} className="ui-cell-title">{device.name}</Link>
    </span>
  );
}

/** Customer Security tab: severe CVEs, firmware to update, end-of-life devices, open issues and compliance. */
export function CustomerSecurity() {
  const { t } = useTranslation();
  const { customerId } = useParams({ from: "/customers/$customerId/security" });
  const query = useCustomerSecurity(customerId);
  const data = query.data;
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <div className="ui-empty">{t("app.loading")}</div>;
  return (
    <>
      <div className="ui-metrics">
        <div className={`ui-metric${data.vulnerabilities.length ? " ui-metric-danger" : ""}`}>
          <span>{t("customerSecurity.cves")}</span><strong>{data.vulnerabilities.length}</strong></div>
        <div className={`ui-metric${data.firmware.length ? " ui-metric-warning" : ""}`}>
          <span>{t("customerSecurity.firmware")}</span><strong>{data.firmware.length}</strong></div>
        <div className={`ui-metric${data.lifecycle.length ? " ui-metric-warning" : ""}`}>
          <span>{t("customerSecurity.lifecycle")}</span><strong>{data.lifecycle.length}</strong></div>
        <div className={`ui-metric${data.issues.length ? " ui-metric-warning" : ""}`}>
          <span>{t("customerSecurity.issues")}</span><strong>{data.issues.length}</strong></div>
        {data.compliance && (
          <div className={`ui-metric${data.compliance.failing_devices ? " ui-metric-danger" : ""}`}>
            <span>{t("customerSecurity.compliance")}</span>
            <strong>{data.compliance.failing_devices}/{data.compliance.evaluated_devices}</strong>
            <small>{t("customerSecurity.failingDevices")}</small>
          </div>
        )}
      </div>
      <Vulnerabilities data={data} customerId={customerId} />
      <div className="ui-two-columns">
        <Firmware data={data} customerId={customerId} />
        <Lifecycle data={data} customerId={customerId} />
      </div>
      <div className="ui-two-columns">
        <Issues data={data} customerId={customerId} />
        {data.compliance && <Compliance data={data} customerId={customerId} />}
      </div>
    </>
  );
}

function Vulnerabilities({ data, customerId }: { data: Data; customerId: string }) {
  const { t, i18n } = useTranslation();
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <div>
          <h2>{t("customerSecurity.cvesTitle")}</h2>
          <p className="ui-muted">{t("customerSecurity.cvesHelp")}</p>
        </div>
        <Link to="/vulnerabilities" search={{ customer: customerId }}>{t("customerSecurity.allVulns")}</Link>
      </div>
      {data.vulnerabilities.length ? (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead><tr>{["cve", "device", "severity", "versions", "detected"].map((key) => <th key={key}><span className="ui-th">{t(`customerSecurity.columns.${key}`)}</span></th>)}</tr></thead>
            <tbody>
              {data.vulnerabilities.map((row) => (
                <tr key={row.id}>
                  <td>
                    <Link to="/vulnerabilities/$advisoryId" params={{ advisoryId: row.advisory.id }} search={{ customer: customerId }} className="ui-cell-title ui-mono">
                      {row.advisory.cve_id}
                    </Link>
                  </td>
                  <td><DeviceCell device={row.device} /></td>
                  <td>
                    <span className={SEVERITY_TONE[row.advisory.severity.toLowerCase()] ?? "ui-severity"}>{t(`vulns.levels.${row.advisory.severity.toLowerCase()}`, { defaultValue: row.advisory.severity })}</span>
                    {row.advisory.cvss != null && <small className="ui-muted"> CVSS {row.advisory.cvss}</small>}
                  </td>
                  <td className="ui-mono">{row.installed_version ?? "—"}{row.fixed_version && <small className="ui-muted ui-block">→ {row.fixed_version}</small>}</td>
                  <td className="ui-nowrap">{formatDateTime(row.detected_at, i18n.language)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ui-empty">{t("customerSecurity.noCves")}</div>
      )}
    </section>
  );
}

function Firmware({ data, customerId }: { data: Data; customerId: string }) {
  const { t } = useTranslation();
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("customerSecurity.firmwareTitle")}</h2>
        <Link to="/firmware" search={{ customer: customerId }}>{t("customerSecurity.allFirmware")}</Link>
      </div>
      {data.firmware.length ? (
        <ul className="ui-list">
          {data.firmware.map((device) => (
            <li key={device.id}>
              <DeviceCell device={device} />
              <span className="ui-mono">{device.firmware_version ?? "—"}{device.recommended_firmware_version && ` → ${device.recommended_firmware_version}`}</span>
              <span className="ui-severity">{t(`firmware.status.${device.firmware_status}`, { defaultValue: device.firmware_status })}</span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="ui-empty">{t("customerSecurity.noFirmware")}</div>
      )}
    </section>
  );
}

function Lifecycle({ data }: { data: Data; customerId: string }) {
  const { t, i18n } = useTranslation();
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("customerSecurity.lifecycleTitle")}</h2>
        <Link to="/lifecycle">{t("customerSecurity.allLifecycle")}</Link>
      </div>
      {data.lifecycle.length ? (
        <ul className="ui-list">
          {data.lifecycle.map((device) => (
            <li key={device.id}>
              <DeviceCell device={device} />
              <span className="ui-muted">{device.model ?? ""}</span>
              <span className="ui-severity ui-severity-high">
                {t(`lifecycle.states.${(device.lifecycle_status ?? "").toLowerCase()}`, { defaultValue: device.lifecycle_status ?? "" })}
                {(device.eos_date || device.eol_date) && ` · ${formatDate(device.eos_date ?? device.eol_date ?? "", i18n.language)}`}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="ui-empty">{t("customerSecurity.noLifecycle")}</div>
      )}
    </section>
  );
}

function Issues({ data, customerId }: { data: Data; customerId: string }) {
  const { t, i18n } = useTranslation();
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("customerSecurity.issuesTitle")}</h2>
        <Link to="/action-center" search={{ customer: customerId }}>{t("nav.actionCenter")}</Link>
      </div>
      {data.issues.length ? (
        <ul className="ui-list">
          {data.issues.map((issue) => (
            <li key={issue.id}>
              <span>
                <strong>{issue.title}</strong>
                <small className="ui-muted ui-block">{t(`actionCenter.categories.${issue.category}`, { defaultValue: issue.category })} · {formatDateTime(issue.updated_at, i18n.language)}</small>
              </span>
              <span />
              <span className={SEVERITY_TONE[issue.severity] ?? "ui-severity"}>{t(`actionCenter.severities.${issue.severity}`, { defaultValue: issue.severity })}</span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="ui-empty">{t("customerSecurity.noIssues")}</div>
      )}
    </section>
  );
}

function Compliance({ data }: { data: Data; customerId: string }) {
  const { t } = useTranslation();
  const compliance = data.compliance;
  if (!compliance) return null;
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("customerSecurity.complianceTitle")}</h2>
        <Link to="/compliance">{t("customerSecurity.allCompliance")}</Link>
      </div>
      <div className="ui-chips">
        {(["pass", "fail", "exception", "unknown"] as const).map((state) => (
          <span key={state} className="ui-chip">{t(`compliance.states.${state}`)} <span className="ui-chip-count">{compliance.counts[state] ?? 0}</span></span>
        ))}
      </div>
      {compliance.failing_controls.length ? (
        <ul className="ui-list">
          {compliance.failing_controls.map((control) => (
            <li key={control.title}>
              <span>{control.title}</span>
              <span />
              <span className="ui-severity ui-severity-high">{t("customerSecurity.failingCount", { count: control.count })}</span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="ui-empty">{compliance.evaluated_devices ? t("customerSecurity.compliant") : t("customerSecurity.notEvaluated")}</div>
      )}
    </section>
  );
}
