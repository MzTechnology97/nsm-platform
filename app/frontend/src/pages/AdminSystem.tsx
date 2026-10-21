import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { type SystemStatus, useSystemStatus } from "../api/admin";
import { AdminTabs } from "../app/AdminTabs";
import { formatBytes, formatDateTime } from "../lib/format";
import { Loading } from "../app/States";

const CONNECTOR_TONE: Record<string, string> = { ok: "online", warning: "warning", error: "offline" };

export function AdminSystem() {
  const { t } = useTranslation();
  const query = useSystemStatus();
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("admin.title")}</h1>
          <p>{t("system.subtitle")}</p>
        </div>
      </header>
      <AdminTabs />
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : (
        <>
          <Version data={data} />
          <Worker data={data} />
          <Connectors data={data} />
          <Capacity data={data} />
          <Secrets data={data} />
          <Dumps data={data} />
          <Drills data={data} />
        </>
      )}
    </section>
  );
}

function Panel({ title, help, badge, children }: { title: string; help?: string; badge?: ReactNode; children: ReactNode }) {
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{title}</h2>
        {badge}
      </div>
      {help && <p className="ui-muted">{help}</p>}
      {children}
    </section>
  );
}

function Badge({ tone, children }: { tone: "online" | "warning" | "offline"; children: ReactNode }) {
  return <span className={`ui-status ui-status-${tone}`}>{children}</span>;
}

function Metric({ label, value, note, warn = false }: { label: string; value: ReactNode; note?: ReactNode; warn?: boolean }) {
  return (
    <div className={`ui-metric${warn ? " ui-metric-warning" : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {note && <small>{note}</small>}
    </div>
  );
}

function Version({ data }: { data: SystemStatus }) {
  const { t, i18n } = useTranslation();
  return (
    <Panel title={t("system.version.title")}>
      <div className="ui-metrics">
        <Metric label={t("system.version.version")} value={data.version} />
        <Metric label={t("system.version.revision")} value={data.database.revision ?? t("system.unreadable")} />
        <Metric label={t("system.version.dbSize")} value={formatBytes(data.database.size, i18n.language)} />
        {data.disk && (
          <Metric label={t("system.version.disk")} value={t("system.version.free", { size: formatBytes(data.disk.free, i18n.language) })}
            note={t("system.version.of", { size: formatBytes(data.disk.total, i18n.language) })} warn={data.disk.low} />
        )}
      </div>
      {data.disk?.low && <div className="ui-alert ui-alert-warning">{t("system.version.lowDisk", { path: data.disk.path })}</div>}
    </Panel>
  );
}

function Worker({ data }: { data: SystemStatus }) {
  const { t, i18n } = useTranslation();
  const { worker } = data;
  const badge = !worker.available
    ? <Badge tone="offline">{t("system.worker.unreadable")}</Badge>
    : worker.alive ? <Badge tone="online">{t("system.worker.alive")}</Badge> : <Badge tone="offline">{t("system.worker.stopped")}</Badge>;
  return (
    <Panel title={t("system.worker.title")} help={t("system.worker.help")} badge={badge}>
      {!worker.available ? (
        <div className="ui-alert ui-alert-warning">{t("system.worker.noRedis")}</div>
      ) : (
        <>
          <div className="ui-metrics">
            <Metric label={t("system.worker.heartbeat")} value={worker.heartbeat ? formatDateTime(worker.heartbeat, i18n.language) : t("adminUsers.never")}
              note={worker.age_seconds !== null ? t("system.worker.secondsAgo", { count: worker.age_seconds }) : undefined} />
            <Metric label={t("system.worker.started")} value={worker.started ? formatDateTime(worker.started, i18n.language) : "—"} />
          </div>
          {!worker.alive && <div className="ui-alert ui-alert-danger">{t("system.worker.down", { seconds: data.stale_seconds })}</div>}
          {worker.tasks.length ? (
            <div className="ui-data-wrap">
              <table className="ui-data-table">
                <thead>
                  <tr>{["task", "lastOk", "duration", "lastError", "failures"].map((key) => <th key={key}><span className="ui-th">{t(`system.worker.columns.${key}`)}</span></th>)}</tr>
                </thead>
                <tbody>
                  {worker.tasks.map((task) => (
                    <tr key={task.name}>
                      <td><strong>{task.name}</strong></td>
                      <td className="ui-nowrap">{task.last_ok ? formatDateTime(task.last_ok, i18n.language) : t("adminUsers.never")}</td>
                      <td>{task.duration_ms !== null ? `${task.duration_ms} ms` : "—"}</td>
                      <td>
                        {task.last_error ? (
                          <>
                            <span className={task.failing ? "ui-key-hint" : "ui-muted"}>{task.last_error}</span>
                            <small className="ui-muted ui-block">{formatDateTime(task.last_error_at, i18n.language)}</small>
                          </>
                        ) : "—"}
                      </td>
                      <td>{task.failing ? <span className="ui-severity ui-severity-high">{task.failures}</span> : 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="ui-empty">{t("system.worker.noTasks")}</div>
          )}
        </>
      )}
    </Panel>
  );
}

function Connectors({ data }: { data: SystemStatus }) {
  const { t, i18n } = useTranslation();
  return (
    <Panel title={t("system.connectors.title")} help={t("system.connectors.help")}>
      <div className="ui-data-wrap">
        <table className="ui-data-table">
          <thead>
            <tr>{["source", "state", "lastOk", "detail"].map((key) => <th key={key}><span className="ui-th">{t(`system.connectors.columns.${key}`)}</span></th>)}</tr>
          </thead>
          <tbody>
            {data.connectors.map((connector) => (
              <tr key={connector.name}>
                <td><a className="ui-cell-title" href={connector.href}>{connector.name}</a></td>
                <td><span className={`ui-status ui-status-${CONNECTOR_TONE[connector.state] ?? "unknown"}`}>{t(`system.connectors.states.${connector.state}`, { defaultValue: connector.state })}</span></td>
                <td className="ui-nowrap">{connector.last_ok ? formatDateTime(connector.last_ok, i18n.language) : "—"}</td>
                <td>{connector.detail}{connector.error && <small className="ui-key-hint ui-block">{connector.error}</small>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function Capacity({ data }: { data: SystemStatus }) {
  const { t, i18n } = useTranslation();
  const { archive } = data;
  const daysLeft = archive.days_left;
  const soon = daysLeft !== null && daysLeft < 90;
  return (
    <Panel title={t("system.capacity.title")} help={t("system.capacity.help")}>
      <div className="ui-metrics">
        <Metric label={t("system.capacity.archive")} value={formatBytes(archive.bytes, i18n.language)} note={t("system.capacity.files", { count: archive.count })} />
        <Metric label={t("system.capacity.growth")} value={formatBytes(archive.growth_30d, i18n.language)} />
        <Metric label={t("system.capacity.left")} warn={soon} note={t("system.capacity.atRate")}
          value={daysLeft === null ? "—" : daysLeft > 3650 ? t("system.capacity.overTenYears") : t("system.capacity.days", { count: daysLeft })} />
      </div>
      {soon && <div className="ui-alert ui-alert-warning">{t("system.capacity.filling", { count: daysLeft ?? 0 })}</div>}
      <div className="ui-two-columns">
        <div className="ui-data-wrap">
          {data.tables.length ? (
            <table className="ui-data-table">
              <thead><tr>{["table", "size", "rows"].map((key) => <th key={key}><span className="ui-th">{t(`system.capacity.columns.${key}`)}</span></th>)}</tr></thead>
              <tbody>
                {data.tables.map((table) => (
                  <tr key={table.name}>
                    <td><code>{table.name}</code></td>
                    <td>{formatBytes(table.bytes, i18n.language)}</td>
                    <td>{new Intl.NumberFormat(i18n.language).format(table.rows)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <div className="ui-empty">{t("system.capacity.noTables")}</div>}
        </div>
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead><tr>{["data", "rule"].map((key) => <th key={key}><span className="ui-th">{t(`system.capacity.columns.${key}`)}</span></th>)}</tr></thead>
            <tbody>
              {data.retention.map((row) => (
                <tr key={row.key}>
                  <td>{t(`system.retention.${row.key}.data`, { defaultValue: row.data })}</td>
                  <td>{t(`system.retention.${row.key}.rule`, { defaultValue: row.rule, count: row.days ?? 0 })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Panel>
  );
}

function Secrets({ data }: { data: SystemStatus }) {
  const { t } = useTranslation();
  const { secrets } = data;
  const badge = secrets.totals.unreadable
    ? <Badge tone="offline">{t("system.secrets.unreadableBadge")}</Badge>
    : secrets.totals.previous ? <Badge tone="warning">{t("system.secrets.rotateBadge")}</Badge> : <Badge tone="online">{t("system.secrets.currentBadge")}</Badge>;
  return (
    <Panel title={t("system.secrets.title")} help={t("system.secrets.help")} badge={badge}>
      <div className="ui-data-wrap">
        <table className="ui-data-table">
          <thead><tr>{["secrets", "current", "previous", "unreadable"].map((key) => <th key={key}><span className="ui-th">{t(`system.secrets.columns.${key}`)}</span></th>)}</tr></thead>
          <tbody>
            {secrets.rows.map((row) => (
              <tr key={row.key}>
                <td>{t(`system.secrets.labels.${row.key.replace(".", "_")}`, { defaultValue: row.label })}</td>
                <td>{row.current}</td>
                <td>{row.previous ? <strong className="ui-key-hint">{row.previous}</strong> : 0}</td>
                <td>{row.unreadable ? <span className="ui-severity ui-severity-high">{row.unreadable}</span> : 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {secrets.totals.previous > 0 ? (
        <div className="ui-alert ui-alert-warning">{t("system.secrets.previous", { count: secrets.totals.previous })}</div>
      ) : secrets.previous_keys > 0 ? (
        <div className="ui-alert">{t("system.secrets.removePrevious", { count: secrets.previous_keys })}</div>
      ) : null}
      {secrets.totals.unreadable > 0 && <div className="ui-alert ui-alert-danger">{t("system.secrets.unreadable", { count: secrets.totals.unreadable })}</div>}
    </Panel>
  );
}

function Dumps({ data }: { data: SystemStatus }) {
  const { t, i18n } = useTranslation();
  const { dumps } = data;
  return (
    <Panel title={t("system.dumps.title")} help={t("system.dumps.help", { folder: dumps.folder, days: data.dump_max_days })}
      badge={<Badge tone={dumps.stale ? "offline" : "online"}>{dumps.stale ? t("system.dumps.stale") : t("system.dumps.recent")}</Badge>}>
      {dumps.latest ? (
        <div className="ui-metrics">
          <Metric label={t("system.dumps.latest")} value={formatDateTime(dumps.latest_at, i18n.language)} note={dumps.latest} />
          <Metric label={t("system.dumps.size")} value={formatBytes(dumps.latest_size, i18n.language)} />
          <Metric label={t("system.dumps.count")} value={dumps.count} note={t("system.dumps.total", { size: formatBytes(dumps.total_size, i18n.language) })} />
        </div>
      ) : (
        <div className="ui-alert ui-alert-warning">{t("system.dumps.none")}</div>
      )}
    </Panel>
  );
}

function Drills({ data }: { data: SystemStatus }) {
  const { t, i18n } = useTranslation();
  const { drills } = data;
  return (
    <Panel title={t("system.drills.title")} help={t("system.drills.help", { days: data.drill_max_days })}
      badge={<Badge tone={drills.overdue ? "offline" : "online"}>{drills.overdue ? t("system.drills.overdue") : t("system.drills.recent")}</Badge>}>
      {drills.entries.length ? (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead><tr>{["when", "result", "file", "duration", "tables", "revision", "devices"].map((key) => <th key={key}><span className="ui-th">{t(`system.drills.columns.${key}`)}</span></th>)}</tr></thead>
            <tbody>
              {drills.entries.map((entry) => (
                <tr key={`${entry.at}-${entry.file ?? ""}`}>
                  <td className="ui-nowrap">{formatDateTime(entry.at, i18n.language)}</td>
                  <td><Badge tone={entry.result === "success" ? "online" : "offline"}>{entry.result === "success" ? t("system.drills.success") : t("system.drills.failed")}</Badge></td>
                  <td>{entry.file ?? "—"}</td>
                  <td>{entry.seconds !== undefined ? `${entry.seconds} s` : "—"}</td>
                  <td>{entry.tables ?? "—"}</td>
                  <td>{entry.revision ?? "—"}</td>
                  <td>{entry.devices ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ui-alert ui-alert-warning">{t("system.drills.none")}</div>
      )}
    </Panel>
  );
}
