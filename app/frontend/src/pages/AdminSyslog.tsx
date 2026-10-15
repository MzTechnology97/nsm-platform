import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { type SyslogAdmin as Data, useSyslogAction, useSyslogAdmin } from "../api/admin";
import { ApiError } from "../api/client";
import { AdminTabs } from "../app/AdminTabs";
import { formatDateTime } from "../lib/format";

export function AdminSyslog() {
  const { t } = useTranslation();
  const query = useSyslogAdmin();
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("admin.title")}</h1>
          <p>{t("syslogAdmin.subtitle")}</p>
        </div>
      </header>
      <AdminTabs />
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <div className="ui-empty">{t("app.loading")}</div>
      ) : (
        <>
          {!data.settings.allowed_networks.length && <div className="ui-alert ui-alert-warning">{t("syslogAdmin.noNetworks")}</div>}
          <Overview data={data} />
          <div className="ui-two-columns">
            <Settings data={data} />
            <Senders data={data} />
          </div>
          <Hardening data={data} />
        </>
      )}
    </section>
  );
}

function Overview({ data }: { data: Data }) {
  const { t } = useTranslation();
  const r = data.receiver;
  const rejected = r?.rejected ?? {};
  const anyRejected = Object.values(rejected).some((value) => value > 0);
  return (
    <>
      <div className="ui-metrics">
        <div className={`ui-metric${r && !r.alive ? " ui-metric-danger" : ""}`}>
          <span>{t("syslogAdmin.receiver")}</span>
          <strong>{!r ? t("syslogAdmin.notStarted") : r.alive ? t("syslogAdmin.alive") : t("syslogAdmin.stopped")}</strong>
          <small>{r ? t("syslogAdmin.receiverNote", { port: r.port ?? 514, seconds: r.age_seconds }) : t("syslogAdmin.dockerService")}</small>
        </div>
        <div className="ui-metric">
          <span>{t("syslogAdmin.storedToday")}</span><strong>{data.stored_today}</strong>
          <small>{t("syslogAdmin.senders", { count: data.senders_today })}</small>
        </div>
        <div className="ui-metric">
          <span>{t("syslogAdmin.sinceRestart")}</span><strong>{r?.received ?? 0}</strong>
          <small>{r ? t("syslogAdmin.storedDiscarded", { stored: r.stored ?? 0, discarded: (r.unknown ?? 0) + (r.dropped ?? 0) }) : t("syslogAdmin.noData")}</small>
        </div>
        <div className="ui-metric">
          <span>{t("syslogAdmin.recognition")}</span><strong>{t("syslogAdmin.keys", { count: r?.keys ?? 0 })}</strong>
          <small>{t("syslogAdmin.sources", { count: r?.sources ?? 0 })}</small>
        </div>
      </div>
      {anyRejected && (
        <section className="ui-panel ui-admin-section">
          <h2>{t("syslogAdmin.rejectedTitle")}</h2>
          <p className="ui-muted">{t("syslogAdmin.rejectedHelp")}</p>
          <dl className="ui-dl">
            {data.reject_reasons.map((key) => <div key={key}><dt>{t(`syslogAdmin.reasons.${key}`, { defaultValue: key })}</dt><dd>{rejected[key] ?? 0}</dd></div>)}
          </dl>
        </section>
      )}
    </>
  );
}

function Settings({ data }: { data: Data }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({
    public_host: data.settings.public_host, info_retention_days: String(data.settings.info_retention_days ?? 30),
    allowed_networks: data.settings.allowed_networks.join("\n"), auto_configure: data.settings.auto_configure,
  });
  const save = useSyslogAction<object>("/settings");
  const configure = useSyslogAction<object, { queued: number; skipped: number; target: string }>("/configure-all");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate({ ...form, info_retention_days: Number(form.info_retention_days) });
  };
  const saveError = save.error instanceof ApiError && save.error.code === "invalid_networks"
    ? t("syslogAdmin.badNetworks", { networks: save.error.message }) : save.error ? (save.error as Error).message : null;
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("syslogAdmin.settings")}</h2>
      <p className="ui-muted">{t("syslogAdmin.settingsHelp")}</p>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit}>
        <label>
          <span>{t("syslogAdmin.publicHost")}</span>
          <input value={form.public_host} onChange={(event) => setForm({ ...form, public_host: event.target.value })} placeholder={data.host || "203.0.113.10"} />
          <small className="ui-muted">{t("syslogAdmin.publicHostHelp", { host: data.host || "—" })}</small>
        </label>
        <label>
          <span>{t("syslogAdmin.retention")}</span>
          <input type="number" min={7} max={90} value={form.info_retention_days} onChange={(event) => setForm({ ...form, info_retention_days: event.target.value })} />
          <small className="ui-muted">{t("syslogAdmin.retentionHelp")}</small>
        </label>
        <label>
          <span>{t("syslogAdmin.networks")}</span>
          <textarea rows={4} value={form.allowed_networks} onChange={(event) => setForm({ ...form, allowed_networks: event.target.value })} placeholder={"203.0.113.0/24\n198.51.100.0/22"} spellCheck={false} />
          <small className="ui-muted">{t("syslogAdmin.networksHelp")}</small>
        </label>
        <p className="ui-muted">{t("syslogAdmin.keyOnly")}</p>
        <label className="ui-with-brand">
          <input type="checkbox" checked={form.auto_configure} onChange={(event) => setForm({ ...form, auto_configure: event.target.checked })} />
          <span>{t("syslogAdmin.autoConfigure")}</span>
        </label>
        <button type="submit" className="ui-button" disabled={save.isPending}>{t("syslogAdmin.save")}</button>
        {save.isSuccess && <small className="ui-live">{t("syslogAdmin.saved")}</small>}
        {saveError && <small className="ui-backup-error">{saveError}</small>}
      </form>
      <div className="ui-admin-form ui-admin-stack">
        <button type="button" className="ui-button ui-button-ghost" disabled={configure.isPending} onClick={() => configure.mutate({})}>{t("syslogAdmin.configureAll")}</button>
        <small className="ui-muted">{t("syslogAdmin.configureAllHelp")}</small>
        {configure.data && <small className="ui-live">{t("syslogAdmin.configured", { queued: configure.data.queued, skipped: configure.data.skipped, target: configure.data.target })}</small>}
        {configure.error && <small className="ui-backup-error">{(configure.error as Error).message}</small>}
      </div>
    </section>
  );
}

function Senders({ data }: { data: Data }) {
  const { t, i18n } = useTranslation();
  const clear = useSyslogAction<{ source_ip: string }>("/unknown/clear");
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("syslogAdmin.discarded")}</h2>
      <p className="ui-muted">{t("syslogAdmin.discardedHelp")}</p>
      {data.unknown_sources.length ? (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead><tr>{["sender", "lines", "last", "reason", "actions"].map((key) => <th key={key}><span className="ui-th">{t(`syslogAdmin.columns.${key}`)}</span></th>)}</tr></thead>
            <tbody>
              {data.unknown_sources.map((source) => (
                <tr key={source.source_ip}>
                  <td><code>{source.source_ip}</code></td>
                  <td>{source.messages}</td>
                  <td className="ui-nowrap">{formatDateTime(source.last_seen, i18n.language)}</td>
                  <td className="ui-audit-details">{source.sample ? <code title={source.sample}>{source.sample}</code> : "—"}</td>
                  <td>
                    <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={clear.isPending} onClick={() => clear.mutate({ source_ip: source.source_ip })}>
                      {t("syslogAdmin.remove")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ui-empty">{t("syslogAdmin.noDiscarded")}</div>
      )}
    </section>
  );
}

function Hardening({ data }: { data: Data }) {
  const { t } = useTranslation();
  const r = data.receiver;
  return (
    <section className="ui-panel ui-admin-section" id="hardening">
      <h2>{t("syslogAdmin.hardening")}</h2>
      <p className="ui-muted">{t("syslogAdmin.hardeningHelp")}</p>
      <dl className="ui-dl">
        <div><dt>{t("syslogAdmin.dbAccount")}</dt><dd>{r?.dedicated_db ? t("syslogAdmin.dbDedicated") : t("syslogAdmin.dbMain")}</dd></div>
        <div><dt>{t("syslogAdmin.container")}</dt><dd>{t("syslogAdmin.containerHelp")}</dd></div>
        <div><dt>{t("syslogAdmin.tls")}</dt><dd>{r?.tls ? t("syslogAdmin.tlsOn") : t("syslogAdmin.tlsOff")}</dd></div>
        <div><dt>{t("syslogAdmin.integrity")}</dt><dd>{t("syslogAdmin.integrityHelp")}</dd></div>
      </dl>
      <h3>{t("syslogAdmin.firewall")}</h3>
      <p className="ui-muted">{t("syslogAdmin.firewallHelp")}</p>
      <pre className="ui-code-block">{data.firewall_script}</pre>
      <p className="ui-muted">{t("syslogAdmin.vendors")} <a href="/admin/syslog#vendors">{t("syslogAdmin.vendorsLink")}</a></p>
    </section>
  );
}
