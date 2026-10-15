import { Link, Outlet, useParams } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import { type CustomerDetail as Detail, useCustomer } from "../api/customer";
import { formatDateTime, formatRelative } from "../lib/format";
import { SiteForm } from "./CustomerForm";
import { Metric } from "./DeviceDetail";

/** Customer tabs already in the new interface; "devices" opens the device list filtered on the customer. */
const MIGRATED_TABS = {
  overview: "/customers/$customerId",
  sites: "/customers/$customerId/sites",
  graphs: "/customers/$customerId/graphs",
  security: "/customers/$customerId/security",
} as const;

export function CustomerDetail() {
  const { t } = useTranslation();
  const { customerId } = useParams({ from: "/customers/$customerId" });
  const query = useCustomer(customerId);
  if (query.isPending) return <div className="ui-empty">{t("app.loading")}</div>;
  if (query.isError) {
    const status = query.error instanceof ApiError ? query.error.status : 0;
    return <div className="ui-empty">{status === 404 ? t("customer.notFound") : t("app.error")}</div>;
  }
  return <CustomerShell detail={query.data} />;
}

function CustomerShell({ detail }: { detail: Detail }) {
  const { t } = useTranslation();
  const { customer, counts } = detail;
  return (
    <section className="ui-page ui-device">
      <nav className="ui-breadcrumbs" aria-label={t("device.breadcrumbs")}>
        <Link to="/customers">{t("nav.customers")}</Link>
        <span aria-hidden="true">›</span>
        <span>{customer.name}</span>
      </nav>
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>
            {customer.name} {!customer.active && <span className="ui-status ui-status-offline">{t("customers.inactive")}</span>}
          </h1>
          <p>
            {[customer.code, t("customers.sites", { count: counts.sites }), t("customer.devices", { count: counts.devices })].filter(Boolean).join(" · ")}
          </p>
          {customer.notes && <p className="ui-muted">{customer.notes}</p>}
        </div>
        <div className="ui-actions">
          {detail.actions.incidents && (
            <Link className="ui-button ui-button-ghost" to="/incidents" search={{ customer: customer.id, status: "all" }}>{t("customer.actions.incidents")}</Link>
          )}
          {detail.actions.edit && (
            <Link className="ui-button ui-button-ghost" to="/customers/$customerId/edit" params={{ customerId: customer.id }}>{t("customer.actions.edit")}</Link>
          )}
          {detail.actions.add_device && (
            <Link className="ui-button ui-button-ghost" to="/customers/$customerId/devices/manage" params={{ customerId: customer.id }}>{t("customer.actions.manageDevices")}</Link>
          )}
          {detail.actions.add_device && (
            <Link className="ui-button ui-button-primary" to="/customers/$customerId/devices/new" params={{ customerId: customer.id }}>{t("customer.actions.addDevice")}</Link>
          )}
        </div>
      </header>
      <nav className="ui-tabs" aria-label={t("customer.tabs.label")}>
        {detail.tabs.map((tab) =>
          tab.key in MIGRATED_TABS ? (
            <Link
              key={tab.key}
              to={MIGRATED_TABS[tab.key as keyof typeof MIGRATED_TABS]}
              params={{ customerId: customer.id }}
              className="ui-tab"
              activeOptions={{ exact: true, includeSearch: false }}
              activeProps={{ className: "ui-tab is-active", "aria-current": "page" }}
            >
              {t(`customer.tabs.${tab.key}`)}
            </Link>
          ) : tab.key === "devices" ? (
            <Link key={tab.key} to="/devices" search={{ customer: customer.id }} className="ui-tab">
              {t("customer.tabs.devices")}
            </Link>
          ) : tab.key === "backups" ? (
            <Link key={tab.key} to="/backups" search={{ customer: customer.id, view: "all" }} className="ui-tab">
              {t("customer.tabs.backups")}
            </Link>
          ) : tab.key === "history" ? (
            <Link key={tab.key} to="/events" search={{ customer: customer.id }} className="ui-tab">
              {t("customer.tabs.history")}
            </Link>
          ) : null,
        )}
      </nav>
      <Outlet />
    </section>
  );
}

export function CustomerOverview() {
  const { t, i18n } = useTranslation();
  const { customerId } = useParams({ from: "/customers/$customerId/" });
  const { data } = useCustomer(customerId);
  if (!data) return null;
  const { metrics, counts } = data;
  const backup = metrics.backup;
  return (
    <div className="ui-syslog">
      <div className="ui-metrics">
        <Metric
          label={t("customer.metrics.devices")}
          value={`${counts.online}/${counts.devices}`}
          hint={counts.offline ? t("customer.offline", { count: counts.offline }) : t("customer.allOnline")}
          tone={counts.offline ? "danger" : undefined}
        />
        <Metric label={t("customer.metrics.severe")} value={String(metrics.severe)} hint={t("customer.hints.severe")} tone={metrics.severe ? "danger" : undefined} />
        <Metric label={t("customer.metrics.updates")} value={String(metrics.updates)} hint={t("customer.hints.updates")} tone={metrics.updates ? "warning" : undefined} />
        <Metric label={t("customer.metrics.alerts")} value={String(metrics.alerts)} hint={t("customer.hints.alerts")} tone={metrics.alerts ? "danger" : undefined} />
        <Metric
          label={t("customer.metrics.backup")}
          value={`${backup.protected}/${backup.devices}`}
          hint={backup.blocked || backup.no_policy ? t("customer.hints.backupGaps", { blocked: backup.blocked, missing: backup.no_policy }) : t("customer.hints.backupOk")}
          tone={backup.blocked || backup.no_policy ? "warning" : undefined}
        />
        <Metric label={t("customer.metrics.lifecycle")} value={String(metrics.lifecycle)} hint={t("customer.hints.lifecycle")} tone={metrics.lifecycle ? "warning" : undefined} />
      </div>

      <div className="ui-quick-links">
        <Link to="/devices" search={{ customer: data.customer.id, status: "offline" }}>{t("customer.links.offline")}</Link>
        <Link to="/devices" search={{ customer: data.customer.id, security: "severe" }}>{t("customer.links.severe")}</Link>
        <Link to="/devices" search={{ customer: data.customer.id, firmware: "attention" }}>{t("customer.links.updates")}</Link>
        <Link to="/devices" search={{ customer: data.customer.id, lifecycle: "eol" }}>{t("customer.links.lifecycle")}</Link>
      </div>

      <section className="ui-panel">
        <div className="ui-panel-header">
          <div>
            <h2>{t("customer.issues.title")}</h2>
            <p className="ui-muted">{t("customer.issues.hint")}</p>
          </div>
          <Link className="ui-button ui-button-ghost" to="/action-center" search={{ customer: data.customer.id }}>{t("customer.issues.all")}</Link>
        </div>
        {data.issues.length ? (
          <ul className="ui-list">
            {data.issues.map((issue) => (
              <li key={issue.id}>
                <span className={`ui-severity ui-severity-${issue.severity}`}>{issue.severity}</span>
                {issue.device_id ? (
                  <Link to="/devices/$deviceId" params={{ deviceId: issue.device_id }}>{issue.title}</Link>
                ) : (
                  <span>{issue.title}</span>
                )}
                <small className="ui-muted">
                  {issue.category} · {formatRelative(issue.updated_at, i18n.language)}
                </small>
              </li>
            ))}
          </ul>
        ) : (
          <p className="ui-muted">{t("customer.issues.none")}</p>
        )}
      </section>

      <section className="ui-panel">
        <div className="ui-panel-header">
          <div>
            <h2>{t("customer.events.title")}</h2>
            <p className="ui-muted">{t("customer.events.hint")}</p>
          </div>
          <Link className="ui-button ui-button-ghost" to="/events" search={{ customer: data.customer.id }}>{t("customer.events.all")}</Link>
        </div>
        {data.events.length ? (
          <div className="ui-log-wrap ui-log-wrap-short">
            <table className="ui-log-table">
              <thead>
                <tr>
                  <th>{t("customer.events.when")}</th>
                  <th>{t("customer.events.event")}</th>
                  <th>{t("customer.events.source")}</th>
                  <th>{t("customer.events.result")}</th>
                </tr>
              </thead>
              <tbody>
                {data.events.map((event, index) => (
                  <tr key={`${event.timestamp}-${index}`}>
                    <td className="ui-nowrap">{formatDateTime(event.timestamp, i18n.language)}</td>
                    <td title={event.type}>{event.label}</td>
                    <td>{event.source ?? "—"}</td>
                    <td>{event.result ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="ui-muted">{t("customer.events.none")}</p>
        )}
      </section>
    </div>
  );
}

export function CustomerSites() {
  const { t } = useTranslation();
  const { customerId } = useParams({ from: "/customers/$customerId/sites" });
  const { data } = useCustomer(customerId);
  const [text, setText] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  if (!data) return null;
  const term = text.trim().toLowerCase();
  const sites = term ? data.sites.filter((site) => site.name.toLowerCase().includes(term) || (site.address ?? "").toLowerCase().includes(term)) : data.sites;

  return (
    <section className="ui-panel">
      <div className="ui-panel-header">
        <div>
          <h2>{t("customer.sites.title", { count: data.sites.length })}</h2>
          <p className="ui-muted">{t("customer.sites.hint")}</p>
        </div>
        {data.actions.add_site && editing !== "new" && (
          <button type="button" className="ui-button ui-button-ghost" onClick={() => setEditing("new")}>{t("customer.sites.add")}</button>
        )}
      </div>
      {editing === "new" && <SiteForm customerId={data.customer.id} onDone={() => setEditing(null)} />}
      {data.sites.length > 5 && (
        <div className="ui-filter-bar">
          <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder={t("customer.sites.search")} aria-label={t("customer.sites.search")} />
        </div>
      )}
      {sites.length ? (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead>
              <tr>
                <th><span className="ui-th">{t("customer.sites.name")}</span></th>
                <th><span className="ui-th">{t("customer.sites.address")}</span></th>
                <th><span className="ui-th">{t("customer.sites.devices")}</span></th>
                <th><span className="ui-th">{t("customer.sites.offline")}</span></th>
                <th />
              </tr>
            </thead>
            <tbody>
              {sites.map((site) => editing === site.id ? (
                <tr key={site.id}>
                  <td colSpan={5}><SiteForm customerId={data.customer.id} site={site} onDone={() => setEditing(null)} /></td>
                </tr>
              ) : (
                <tr key={site.id}>
                  <td>
                    <strong>{site.name}</strong>
                    {site.notes && <small className="ui-muted ui-block">{site.notes}</small>}
                  </td>
                  <td>{site.address ?? "—"}</td>
                  <td>
                    <Link to="/devices" search={{ customer: data.customer.id, site: site.id }} className="ui-cell-link">
                      <strong>{site.devices}</strong> <small className="ui-muted">{t("customers.online", { count: site.online })}</small>
                    </Link>
                  </td>
                  <td>
                    <Link to="/devices" search={{ customer: data.customer.id, site: site.id, status: "offline" }} className={`ui-cell-link${site.offline ? " is-danger" : ""}`}>
                      <strong>{site.offline}</strong>
                    </Link>
                  </td>
                  <td>
                    <div className="ui-row-actions">
                      <Link className="ui-button ui-button-ghost ui-button-small" to="/customers/$customerId/graphs" params={{ customerId: data.customer.id }} search={{ site: site.id }}>
                        {t("customer.sites.graphs")}
                      </Link>
                      {data.actions.edit && (
                        <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={() => setEditing(site.id)}>{t("customer.sites.edit")}</button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ui-empty">{term ? t("customer.sites.noMatch") : t("customer.sites.empty")}</div>
      )}
      {data.counts.unassigned > 0 && (
        <p className="ui-muted">
          {t("customer.sites.unassigned", { count: data.counts.unassigned })}
        </p>
      )}
    </section>
  );
}
