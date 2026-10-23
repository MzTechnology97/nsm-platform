import { useMemo, useState } from "react";
import { Trans, useTranslation } from "react-i18next";

import { type ServerMigrationState, useServerMigration, useServerMigrationAction, useServerMigrationCreate } from "../api/admin";
import { ApiError } from "../api/client";
import { AdminTabs } from "../app/AdminTabs";
import { Loading } from "../app/States";
import { formatDateTime } from "../lib/format";

const CHECKLIST = ["install", "create", "restore", "settings", "pilot", "reinstall"] as const;
const PILOT_MAX = 10;
const TONE: Record<string, string> = {
  arrived: "ui-status ui-status-online", sent: "ui-status ui-status-success", queued: "ui-status ui-status-pending",
  failed: "ui-status ui-status-offline", reinstall: "ui-status ui-status-warning",
};

function errorText(error: unknown) {
  return error instanceof ApiError ? error.message : (error as Error | null)?.message;
}

/** Move NSM to a server with another address: the old server tells agents and TR-069 CPEs the new one. */
export function AdminServerMigration() {
  const { t } = useTranslation();
  const query = useServerMigration();
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("admin.title")}</h1>
          <p>{t("serverMigration.subtitle")}</p>
        </div>
      </header>
      <AdminTabs />
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : (
        <>
          <section className="ui-panel ui-admin-section">
            <h2>{t("serverMigration.howTitle")}</h2>
            <p>{t("serverMigration.how")}</p>
            <ol className="ui-acs-steps">
              {CHECKLIST.map((step) => (
                <li key={step}><Trans i18nKey={`serverMigration.steps.${step}`} components={{ code: <code /> }} /></li>
              ))}
            </ol>
          </section>
          <Readiness data={data} />
          {data.migration ? <Running data={data} /> : <CreateForm />}
        </>
      )}
    </section>
  );
}

function Readiness({ data }: { data: ServerMigrationState }) {
  const { t } = useTranslation();
  const r = data.readiness;
  return (
    <div className="ui-metrics">
      <div className="ui-metric"><span>{t("serverMigration.readiness.capable")}</span><strong>{r.agents_capable}</strong><small>{t("serverMigration.readiness.capableHint")}</small></div>
      <div className="ui-metric"><span>{t("serverMigration.readiness.reinstall")}</span><strong>{r.agents_reinstall}</strong><small>{t("serverMigration.readiness.reinstallHint")}</small></div>
      <div className="ui-metric"><span>{t("serverMigration.readiness.tr069")}</span><strong>{r.tr069}</strong><small>{t("serverMigration.readiness.tr069Hint")}</small></div>
    </div>
  );
}

function CreateForm() {
  const { t } = useTranslation();
  const create = useServerMigrationCreate();
  const [agentUrl, setAgentUrl] = useState("");
  const [acsUrl, setAcsUrl] = useState("");
  const suggestedAcs = useMemo(() => {
    try {
      const host = new URL(agentUrl.includes("://") ? agentUrl : `http://${agentUrl}`).hostname;
      return host ? `http://${host}:7547` : "";
    } catch {
      return "";
    }
  }, [agentUrl]);
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("serverMigration.createTitle")}</h2>
      <div className="ui-admin-form ui-admin-stack">
        <label>{t("serverMigration.fields.agentUrl")}<input type="text" placeholder="http://198.51.100.20" value={agentUrl} onChange={(e) => setAgentUrl(e.target.value)} /></label>
        <label>{t("serverMigration.fields.acsUrl")}<input type="text" placeholder={suggestedAcs || "http://198.51.100.20:7547"} value={acsUrl} onChange={(e) => setAcsUrl(e.target.value)} /></label>
      </div>
      <p className="ui-muted">{t("serverMigration.createHelp")}</p>
      {create.error && <div className="ui-alert ui-alert-danger">{errorText(create.error)}</div>}
      <div className="ui-row-actions">
        <button type="button" className="ui-button ui-button-primary" disabled={!agentUrl.trim() || create.isPending}
          onClick={() => create.mutate({ agent_base_url: agentUrl.trim(), acs_url: (acsUrl.trim() || suggestedAcs) })}>
          {t("serverMigration.create")}
        </button>
      </div>
    </section>
  );
}

function Running({ data }: { data: ServerMigrationState }) {
  const { t, i18n } = useTranslation();
  const migration = data.migration!;
  const verify = useServerMigrationAction(migration.id, "verify");
  const start = useServerMigrationAction(migration.id, "start");
  const confirm = useServerMigrationAction(migration.id, "confirm");
  const cancel = useServerMigrationAction(migration.id, "cancel");
  const retire = useServerMigrationAction(migration.id, "retire");
  const [selected, setSelected] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const done = new Set(data.items.filter((i) => ["queued", "sent", "arrived"].includes(i.status)).map((i) => i.device_id));
  const matches = data.candidates
    .filter((c) => !done.has(c.id))
    .filter((c) => !filter.trim() || `${c.name ?? ""} ${c.customer ?? ""}`.toLowerCase().includes(filter.trim().toLowerCase()))
    .slice(0, 50);
  const error = [verify, start, confirm, cancel, retire].map((m) => m.error).find(Boolean);
  const busy = [verify, start, confirm, cancel, retire].some((m) => m.isPending);
  const toggle = (id: string) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : s.length >= PILOT_MAX ? s : [...s, id]));
  const arrived = migration.counts.arrived ?? 0;
  return (
    <>
      <section className="ui-panel ui-admin-section">
        <div className="ui-page-header-row">
          <h2>{t("serverMigration.runningTitle")}</h2>
          <button type="button" className="ui-button ui-button-quiet-danger" disabled={busy}
            onClick={() => window.confirm(t("serverMigration.cancelConfirm")) && cancel.mutate({})}>{t("serverMigration.cancel")}</button>
        </div>
        <dl className="ui-dl">
          <div><dt>{t("serverMigration.fields.agentUrl")}</dt><dd className="ui-mono">{migration.agent_base_url}</dd></div>
          <div><dt>{t("serverMigration.fields.acsUrl")}</dt><dd className="ui-mono">{migration.acs_url ?? "—"}</dd></div>
          <div><dt>{t("serverMigration.fields.syslog")}</dt><dd className="ui-mono">{migration.syslog_ip ?? t("serverMigration.noSyslog")}</dd></div>
          <div><dt>{t("serverMigration.fields.created")}</dt><dd>{formatDateTime(migration.created_at, i18n.language)} · {migration.created_by ?? "—"}</dd></div>
        </dl>
        {error && <div className="ui-alert ui-alert-danger">{errorText(error)}</div>}
      </section>

      <section className="ui-panel ui-admin-section">
        <div className="ui-page-header-row">
          <div>
            <h2>{t("serverMigration.verifyTitle")}</h2>
            <p className="ui-muted">{migration.verify_valid
              ? t("serverMigration.verified", { when: formatDateTime(migration.verified_at, i18n.language), version: migration.target_check?.version ?? "—", devices: migration.target_check?.devices ?? 0 })
              : t("serverMigration.verifyHelp")}</p>
          </div>
          <button type="button" className="ui-button" disabled={busy} onClick={() => verify.mutate({})}>{t("serverMigration.verify")}</button>
        </div>
      </section>

      <section className="ui-panel ui-admin-section">
        <h2>{t("serverMigration.pilotTitle")}</h2>
        <p className="ui-muted">{t("serverMigration.pilotHelp", { max: PILOT_MAX })}</p>
        <div className="ui-admin-form ui-admin-stack">
          <label>{t("serverMigration.search")}<input type="search" value={filter} onChange={(e) => setFilter(e.target.value)} /></label>
        </div>
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead><tr><th /><th><span className="ui-th">{t("serverMigration.cols.device")}</span></th><th><span className="ui-th">{t("serverMigration.cols.customer")}</span></th><th><span className="ui-th">{t("serverMigration.cols.kind")}</span></th><th><span className="ui-th">{t("serverMigration.cols.online")}</span></th></tr></thead>
            <tbody>
              {matches.map((c) => (
                <tr key={c.id}>
                  <td><input type="checkbox" aria-label={c.name ?? c.id} checked={selected.includes(c.id)} onChange={() => toggle(c.id)} /></td>
                  <td>{c.name ?? "—"}</td>
                  <td>{c.customer ?? "—"}</td>
                  <td>{t(`serverMigration.kind.${c.kind}`)}</td>
                  <td>{c.online ? t("serverMigration.yes") : t("serverMigration.no")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="ui-row-actions">
          <button type="button" className="ui-button" disabled={busy || !selected.length || !migration.verify_valid}
            onClick={() => start.mutate({ device_ids: selected }, { onSuccess: () => setSelected([]) })}>{t("serverMigration.startPilot", { count: selected.length })}</button>
          <button type="button" className="ui-button ui-button-primary" disabled={busy || !migration.pilot_arrived || !migration.verify_valid}
            onClick={() => window.confirm(t("serverMigration.startAllConfirm")) && start.mutate({})}>{t("serverMigration.startAll")}</button>
        </div>
        {!migration.pilot_arrived && <p className="ui-muted">{t("serverMigration.allNeedsPilot")}</p>}
      </section>

      <section className="ui-panel ui-admin-section">
        <div className="ui-page-header-row">
          <div>
            <h2>{t("serverMigration.itemsTitle")}</h2>
            <p className="ui-muted">{(["arrived", "sent", "queued", "failed", "reinstall"] as const)
              .map((key) => `${t(`serverMigration.status.${key}`)}: ${migration.counts[key] ?? 0}`).join(" · ")}</p>
          </div>
          <div className="ui-row-actions">
            <button type="button" className="ui-button" disabled={busy || !data.items.length} onClick={() => confirm.mutate({})}>{t("serverMigration.confirm")}</button>
            <button type="button" className="ui-button ui-button-quiet-danger" disabled={busy || !arrived}
              onClick={() => window.confirm(t("serverMigration.retireConfirm", { count: arrived })) && retire.mutate({})}>{t("serverMigration.retire")}</button>
          </div>
        </div>
        {data.items.length === 0 ? <p className="ui-muted">{t("serverMigration.noItems")}</p> : (
          <div className="ui-data-wrap">
            <table className="ui-data-table">
              <thead><tr>{(["device", "customer", "kind", "status", "detail", "arrived"] as const).map((k) => <th key={k}><span className="ui-th">{t(`serverMigration.cols.${k}`)}</span></th>)}</tr></thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.device_id}>
                    <td>{item.name ?? "—"}{item.decommissioned && <small className="ui-muted ui-block">{t("serverMigration.retired")}</small>}</td>
                    <td>{item.customer ?? "—"}</td>
                    <td>{t(`serverMigration.kind.${item.kind}`)}</td>
                    <td><span className={TONE[item.status] ?? "ui-status"}>{t(`serverMigration.status.${item.status}`, { defaultValue: item.status })}</span></td>
                    <td className="ui-muted">{item.detail ?? "—"}</td>
                    <td>{item.arrived_at ? formatDateTime(item.arrived_at, i18n.language) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
