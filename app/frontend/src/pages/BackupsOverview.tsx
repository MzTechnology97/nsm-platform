import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { SearchSelect } from "../app/SearchSelect";

import { useDeviceFacets } from "../api/devices";
import { BrandIcon } from "../app/BrandIcon";
import { BACKUP_VIEWS, type BackupOverviewFilters, type BackupOverviewRow, useBackupOverview } from "../api/operations";
import { describeCron } from "../lib/cron";
import { formatDateTime, formatRelative } from "../lib/format";
import { useDebounced } from "../lib/hooks";
import { Metric } from "./DeviceDetail";
import { Loading } from "../app/States";
import { Pager } from "../app/Pager";
import { pageParams, usePaging } from "../lib/paging";

const BUCKET_TONE = { protected: "ui-severity", blocked: "ui-severity ui-severity-warning", no_policy: "ui-severity ui-severity-high" } as const;

export function BackupsOverview() {
  const { t, i18n } = useTranslation();
  const search = useSearch({ from: "/backups" });
  const navigate = useNavigate({ from: "/backups" });
  const facets = useDeviceFacets();
  const [text, setText] = useState(search.q ?? "");
  const q = useDebounced(text.trim());
  const update = (patch: Partial<BackupOverviewFilters>) => void navigate({ search: (previous: BackupOverviewFilters) => ({ ...previous, ...patch }), replace: true });
  useEffect(() => {
    if ((search.q ?? "") !== q) update({ q: q || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);
  const paging = usePaging(JSON.stringify(search));
  const query = useBackupOverview(search, pageParams(paging));
  const data = query.data;
  const view = search.view ?? "attention";

  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("backupOverview.title")}</h1>
          <p>{t("backupOverview.subtitle")}</p>
        </div>
        <div className="ui-actions">
          {data?.can_configure && (
            <Link to="/backups/policies/new" search={search.customer ? { customer: search.customer } : {}} className="ui-button ui-button-primary">{t("backupOverview.newPolicy")}</Link>
          )}
        </div>
      </header>

      {data && (
        <div className="ui-metrics">
          <Metric label={t("backupOverview.metrics.protected")} value={`${data.totals.protected}/${data.totals.devices}`} hint={t("backupOverview.hints.protected")} />
          <Metric label={t("backupOverview.metrics.blocked")} value={String(data.totals.blocked)} hint={t("backupOverview.hints.blocked")} tone={data.totals.blocked ? "warning" : undefined} />
          <Metric label={t("backupOverview.metrics.noPolicy")} value={String(data.totals.no_policy)} hint={t("backupOverview.hints.noPolicy")} tone={data.totals.no_policy ? "danger" : undefined} />
          <Metric label={t("backupOverview.metrics.alerts")} value={String(data.totals.backup_alerts)} hint={t("backupOverview.hints.alerts")} tone={data.totals.backup_alerts ? "danger" : undefined} />
          <Metric label={t("backupOverview.metrics.policies")} value={String(data.totals.active_policies)} hint={t("backupOverview.hints.policies", { count: data.policies.length })} />
        </div>
      )}

      <div className="ui-chips" role="group" aria-label={t("backupOverview.views.label")}>
        {BACKUP_VIEWS.map((key) => (
          <button
            key={key}
            type="button"
            className={`ui-chip${["failed", "blocked", "never", "no_policy"].includes(key) ? " ui-chip-warning" : ""}${view === key ? " is-active" : ""}`}
            onClick={() => update({ view: key === "attention" ? undefined : key })}
          >
            {t(`backupOverview.views.${key}`)} <span className="ui-chip-count">{data ? data.counts[key] ?? 0 : "—"}</span>
          </button>
        ))}
      </div>
      <div className="ui-filter-bar">
        <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder={t("backupOverview.search")} aria-label={t("backupOverview.search")} autoComplete="off" />
        <SearchSelect value={search.customer ?? ""} options={(facets.data?.customers ?? []).map((customer) => ({ value: customer.id, label: customer.name }))} onChange={(value) => update({ customer: value || undefined })} emptyLabel={t("devices.allCustomers")} ariaLabel={t("devices.customer")} />
      </div>

      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : data.items.length ? (
        <>
          <div className="ui-data-wrap" aria-busy={query.isFetching}>
            <table className="ui-data-table">
              <thead>
                <tr>
                  {["device", "protection", "policy", "latest"].map((column) => (
                    <th key={column}><span className="ui-th">{t(`backupOverview.columns.${column}`)}</span></th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.items.map((row) => <Row key={row.id} row={row} canConfigure={data.can_configure} />)}
              </tbody>
            </table>
          </div>
          <Pager total={data.total} paging={paging} busy={query.isFetching} />
        </>
      ) : (
        <div className="ui-empty">{view === "attention" ? t("backupOverview.allGood") : t("backupOverview.empty")}</div>
      )}

      {data && (
        <div className="ui-two-columns ui-section-gap">
          <section className="ui-panel">
            <h2>{t("backupOverview.coverage.title")}</h2>
            <div className="ui-table-scroll">
              <table className="ui-stats-table">
                <thead>
                  <tr>
                    <th>{t("backupOverview.coverage.customer")}</th>
                    <th>{t("backupOverview.coverage.protected")}</th>
                    <th>{t("backupOverview.coverage.blocked")}</th>
                    <th>{t("backupOverview.coverage.noPolicy")}</th>
                    <th>{t("backupOverview.coverage.alerts")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.customers.map((row) => (
                    <tr key={row.id}>
                      <td><Link to="/customers/$customerId" params={{ customerId: row.id }}>{row.name}</Link></td>
                      <td>{row.protected}/{row.devices}</td>
                      <td className={row.blocked ? "ui-text-warning" : undefined}>{row.blocked}</td>
                      <td className={row.no_policy ? "ui-text-danger" : undefined}>{row.no_policy}</td>
                      <td className={row.backup_alerts ? "ui-text-danger" : undefined}>{row.backup_alerts}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section className="ui-panel">
            <h2>{t("backupOverview.policies.title")}</h2>
            <p className="ui-muted">{t("backupOverview.policies.hint")}</p>
            {data.policies.length ? (
              <ul className="ui-cap-list">
                {data.policies.map((policy) => (
                  <li key={policy.id}>
                    <div>
                      {data.can_configure ? <Link to="/backups/policies/$policyId/edit" params={{ policyId: policy.id }}>{policy.name}</Link> : <strong>{policy.name}</strong>}
                      <small className="ui-muted">{policy.scope} · {describeCron(policy.schedule, t, i18n.language)}</small>
                    </div>
                    <span className={policy.enabled ? "ui-severity" : "ui-severity ui-severity-warning"}>{policy.enabled ? t("backupOverview.policies.on") : t("backupOverview.policies.off")}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="ui-muted">{t("backupOverview.policies.none")}</p>
            )}
          </section>
        </div>
      )}
    </section>
  );
}

function Row({ row, canConfigure }: { row: BackupOverviewRow; canConfigure: boolean }) {
  const { t, i18n } = useTranslation();
  const latest = row.latest;
  return (
    <tr>
      <td>
        <div className="ui-with-brand">
          <BrandIcon brand={row.brand} small />
          <span>
            <Link to="/devices/$deviceId/backups" params={{ deviceId: row.id }} className="ui-cell-title">{row.name}</Link>
            <small className="ui-muted ui-block">{[row.customer?.name, row.site, row.vendor].filter(Boolean).join(" · ")}</small>
          </span>
        </div>
      </td>
      <td>
        <span className={BUCKET_TONE[row.bucket]}>{t(`backupOverview.readiness.${row.readiness.status}`, { defaultValue: row.readiness.label })}</span>
        <small className="ui-muted ui-block" title={row.readiness.reason}>{row.readiness.method}</small>
      </td>
      <td>
        {row.policy ? (
          canConfigure ? <Link to="/backups/policies/$policyId/edit" params={{ policyId: row.policy.id }}>{row.policy.name}</Link> : row.policy.name
        ) : canConfigure && row.customer ? (
          <Link to="/backups/policies/new" search={{ customer: row.customer.id, device: row.id }} className="ui-button ui-button-ghost ui-button-small">{t("backupOverview.createPolicy")}</Link>
        ) : (
          <span className="ui-muted">—</span>
        )}
      </td>
      <td>
        {latest ? (
          <>
            <span className={latest.status === "success" ? "ui-status ui-status-online" : latest.status === "failed" ? "ui-status ui-status-offline" : "ui-status ui-status-pending"}>
              {t(`backups.status.${latest.status}`, { defaultValue: latest.status })}
            </span>{" "}
            <span title={formatDateTime(latest.completed_at ?? latest.started_at, i18n.language)}>{formatRelative(latest.completed_at ?? latest.started_at, i18n.language)}</span>
            {latest.error && <small className="ui-backup-error ui-block">{latest.error}</small>}
          </>
        ) : (
          <span className="ui-text-warning">{t("backupOverview.never")}</span>
        )}
      </td>
    </tr>
  );
}
