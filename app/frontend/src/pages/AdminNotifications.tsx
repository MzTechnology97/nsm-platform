import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { type NotificationAdmin, useNotificationAction, useNotificationAdmin } from "../api/admin";
import { AdminTabs } from "../app/AdminTabs";
import { formatDateTime } from "../lib/format";

const STATUS_FILTERS = ["", "pending", "sent", "failed"] as const;
const DELIVERY_TONE: Record<string, string> = { sent: "online", failed: "offline", pending: "warning" };

export function AdminNotifications() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<string>("");
  const query = useNotificationAdmin(status);
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("admin.title")}</h1>
          <p>{t("notificationsAdmin.subtitle")}</p>
        </div>
      </header>
      <AdminTabs />
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <div className="ui-empty">{t("app.loading")}</div>
      ) : (
        <>
          <div className="ui-two-columns">
            <SmtpForm data={data} />
            <Health data={data} />
          </div>
          <Telegram data={data} />
          <Log data={data} status={status} onStatus={setStatus} busy={query.isFetching} />
        </>
      )}
    </section>
  );
}

function SmtpForm({ data }: { data: NotificationAdmin }) {
  const { t } = useTranslation();
  const { smtp } = data;
  const [form, setForm] = useState({
    host: smtp.host, port: String(smtp.port || 587), security: smtp.security || "starttls", username: smtp.username, password: "",
    from_address: smtp.from_address, from_name: smtp.from_name || "NSM", portal_url: smtp.portal_url, enabled: smtp.configured ? smtp.enabled : true,
  });
  const save = useNotificationAction<object>("/smtp");
  const set = (key: keyof typeof form) => (event: { target: { value: string } }) => setForm({ ...form, [key]: event.target.value });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate({ ...form, port: Number(form.port) }, { onSuccess: () => setForm({ ...form, password: "" }) });
  };
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("notificationsAdmin.smtp.title")}</h2>
        <span className={`ui-status ui-status-${smtp.enabled ? "online" : "offline"}`}>{smtp.enabled ? t("notificationsAdmin.enabled") : t("notificationsAdmin.inactive")}</span>
      </div>
      <p className="ui-muted">{t("notificationsAdmin.smtp.help")}</p>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit} autoComplete="off">
        <label><span>{t("notificationsAdmin.smtp.host")}</span><input value={form.host} onChange={set("host")} required placeholder="smtp.example.net" /></label>
        <label><span>{t("notificationsAdmin.smtp.port")}</span><input type="number" min={1} max={65535} value={form.port} onChange={set("port")} required /></label>
        <label>
          <span>{t("notificationsAdmin.smtp.security")}</span>
          <select value={form.security} onChange={set("security")}>
            {data.security_modes.map((mode) => <option key={mode} value={mode}>{t(`notificationsAdmin.smtp.modes.${mode}`, { defaultValue: mode })}</option>)}
          </select>
        </label>
        <label><span>{t("notificationsAdmin.smtp.username")}</span><input value={form.username} onChange={set("username")} autoComplete="off" /><small className="ui-muted">{t("notificationsAdmin.smtp.usernameHelp")}</small></label>
        <label>
          <span>{t("notificationsAdmin.smtp.password")}</span>
          <input type="password" value={form.password} onChange={set("password")} autoComplete="new-password" placeholder={smtp.configured ? t("notificationsAdmin.keepSecret") : ""} />
        </label>
        <label><span>{t("notificationsAdmin.smtp.from")}</span><input type="email" value={form.from_address} onChange={set("from_address")} required placeholder="nsm@example.net" /></label>
        <label><span>{t("notificationsAdmin.smtp.fromName")}</span><input value={form.from_name} onChange={set("from_name")} /></label>
        <label><span>{t("notificationsAdmin.smtp.portal")}</span><input type="url" value={form.portal_url} onChange={set("portal_url")} placeholder="https://nsm.example.net" /><small className="ui-muted">{t("notificationsAdmin.smtp.portalHelp")}</small></label>
        <label className="ui-with-brand"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /> <span>{t("notificationsAdmin.smtp.enable")}</span></label>
        <button type="submit" className="ui-button" disabled={save.isPending}>{t("notificationsAdmin.smtp.save")}</button>
        {save.isSuccess && <small className="ui-live">{t("notificationsAdmin.smtp.saved")}</small>}
        {save.error && <small className="ui-backup-error">{(save.error as Error).message}</small>}
      </form>
    </section>
  );
}

function Health({ data }: { data: NotificationAdmin }) {
  const { t, i18n } = useTranslation();
  const [to, setTo] = useState("");
  const test = useNotificationAction<{ to: string }, { result: string; message: string }>("/smtp/test");
  const { health, smtp } = data;
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("notificationsAdmin.health.title")}</h2>
      <p className="ui-muted">{t("notificationsAdmin.health.help")}</p>
      <div className="ui-metrics">
        <div className="ui-metric"><span>{t("notificationsAdmin.health.sent")}</span><strong>{health.sent}</strong></div>
        <div className={`ui-metric${health.stuck ? " ui-metric-warning" : ""}`}>
          <span>{t("notificationsAdmin.health.pending")}</span><strong>{health.pending}</strong>
          {health.stuck > 0 && <small>{t("notificationsAdmin.health.stuck", { count: health.stuck })}</small>}
        </div>
        <div className={`ui-metric${health.failed ? " ui-metric-danger" : ""}`}><span>{t("notificationsAdmin.health.failed")}</span><strong>{health.failed}</strong></div>
        <div className="ui-metric"><span>{t("notificationsAdmin.health.subscribers")}</span><strong>{data.subscribers}</strong><small>{t("notificationsAdmin.health.subscribersHelp")}</small></div>
      </div>
      {smtp.configured && (
        <form className="ui-admin-form" onSubmit={(event) => { event.preventDefault(); test.mutate({ to }); }}>
          <label className="ui-admin-wide">
            <span>{t("notificationsAdmin.health.testTo")}</span>
            <input type="email" value={to} onChange={(event) => setTo(event.target.value)} placeholder={t("notificationsAdmin.health.testPlaceholder")} />
          </label>
          <button type="submit" className="ui-button" disabled={test.isPending}>{t("notificationsAdmin.health.test")}</button>
          {test.data && <small className={test.data.result === "success" ? "ui-live" : "ui-backup-error"}>{test.data.message}</small>}
          {test.error && <small className="ui-backup-error">{(test.error as Error).message}</small>}
        </form>
      )}
      {smtp.last_tested_at && (
        <p className="ui-muted">
          {t(smtp.last_test_status === "success" ? "notificationsAdmin.health.lastOk" : "notificationsAdmin.health.lastFailed", { when: formatDateTime(smtp.last_tested_at, i18n.language) })}
          {smtp.last_error && ` — ${smtp.last_error}`}
        </p>
      )}
    </section>
  );
}

function Telegram({ data }: { data: NotificationAdmin }) {
  const { t } = useTranslation();
  const { telegram } = data;
  const [token, setToken] = useState("");
  const [enabled, setEnabled] = useState(telegram.configured ? telegram.enabled : true);
  const save = useNotificationAction<object, { verified: boolean; message: string }>("/telegram");
  const active = telegram.enabled && telegram.last_test_status === "success";
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("notificationsAdmin.chat.title")}</h2>
        {telegram.configured && (
          <span className={`ui-status ui-status-${active ? "online" : "offline"}`}>{active ? t("notificationsAdmin.chat.bot", { name: telegram.bot }) : t("notificationsAdmin.chat.botInactive")}</span>
        )}
      </div>
      <p className="ui-muted">{t("notificationsAdmin.chat.help")}</p>
      <form className="ui-admin-form" autoComplete="off"
        onSubmit={(event) => { event.preventDefault(); save.mutate({ token, enabled }, { onSuccess: () => setToken("") }); }}>
        <label className="ui-admin-wide">
          <span>{t("notificationsAdmin.chat.token")}</span>
          <input type="password" value={token} onChange={(event) => setToken(event.target.value)} autoComplete="off"
            placeholder={telegram.configured ? t("notificationsAdmin.keepSecret") : t("notificationsAdmin.chat.tokenPlaceholder")} />
        </label>
        <label className="ui-with-brand"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /> <span>{t("notificationsAdmin.chat.enable")}</span></label>
        <button type="submit" className="ui-button" disabled={save.isPending}>{t("notificationsAdmin.chat.save")}</button>
        {save.data && <small className={save.data.verified ? "ui-live" : "ui-backup-error"}>{save.data.message}</small>}
        {save.error && <small className="ui-backup-error">{(save.error as Error).message}</small>}
      </form>
      {telegram.last_error && !save.data && <p className="ui-key-hint">{telegram.last_error}</p>}
    </section>
  );
}

function Log({ data, status, onStatus, busy }: { data: NotificationAdmin; status: string; onStatus: (status: string) => void; busy: boolean }) {
  const { t, i18n } = useTranslation();
  const retry = useNotificationAction<object, { requeued: number }>("/retry");
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("notificationsAdmin.log.title")}</h2>
        {data.health.failed > 0 && (
          <button type="button" className="ui-button" disabled={retry.isPending} onClick={() => retry.mutate({})}>{t("notificationsAdmin.log.retry")}</button>
        )}
      </div>
      <p className="ui-muted">{t("notificationsAdmin.log.help")}</p>
      {retry.data && <p className="ui-live">{t("notificationsAdmin.log.requeued", { count: retry.data.requeued })}</p>}
      <div className="ui-chips" role="group" aria-label={t("notificationsAdmin.log.filter")}>
        {STATUS_FILTERS.map((key) => (
          <button key={key || "all"} type="button" className={`ui-chip${status === key ? " is-active" : ""}`} onClick={() => onStatus(key)}>
            {t(`notificationsAdmin.log.filters.${key || "all"}`)}
          </button>
        ))}
      </div>
      {data.deliveries.length ? (
        <div className="ui-data-wrap" aria-busy={busy}>
          <table className="ui-data-table">
            <thead><tr>{["when", "recipient", "channel", "message", "status"].map((key) => <th key={key}><span className="ui-th">{t(`notificationsAdmin.log.columns.${key}`)}</span></th>)}</tr></thead>
            <tbody>
              {data.deliveries.map((row) => (
                <tr key={row.id}>
                  <td className="ui-nowrap">{formatDateTime(row.created_at, i18n.language)}</td>
                  <td>{row.user ?? "—"}{row.destination && <small className="ui-muted ui-block">{row.destination}</small>}</td>
                  <td>{t(`notificationsAdmin.channels.${row.channel}`, { defaultValue: row.channel })}</td>
                  <td>
                    {row.subject}
                    <small className="ui-muted ui-block">
                      {t(`notificationsAdmin.categories.${row.category}`, { defaultValue: row.category })} · {t(`notificationsAdmin.levels.${row.severity}`, { defaultValue: row.severity })}
                    </small>
                  </td>
                  <td>
                    <span className={`ui-status ui-status-${DELIVERY_TONE[row.status] ?? "unknown"}`}>{t(`notificationsAdmin.log.states.${row.status}`, { defaultValue: row.status })}</span>
                    <small className="ui-muted ui-block">{row.status === "sent" ? formatDateTime(row.sent_at, i18n.language) : t("notificationsAdmin.log.attempts", { count: row.attempts })}</small>
                    {row.last_error && <small className="ui-key-hint ui-block">{row.last_error}</small>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ui-empty">{t("notificationsAdmin.log.none")}</div>
      )}
    </section>
  );
}
