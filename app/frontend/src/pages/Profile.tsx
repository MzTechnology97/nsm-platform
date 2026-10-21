import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, setCsrfToken } from "../api/client";
import { type ChannelPreference, type Profile as ProfileData, useProfile, useProfileAction } from "../api/personal";
import { useSession, useUpdatePreferences } from "../api/session";
import { formatDateTime } from "../lib/format";
import { Loading } from "../app/States";

const DELIVERY_TONE: Record<string, string> = { sent: "online", failed: "offline", pending: "warning" };

/** Server error translated by its code (``profile.errors.<code>``). */
function useErrorText() {
  const { t } = useTranslation();
  return (error: unknown) =>
    error instanceof ApiError ? t(`profile.errors.${error.code ?? "generic"}`, { defaultValue: error.message }) : error ? String(error) : null;
}

/** Personal profile: account, password, sessions, second factor, notification channels, newsletter and theme. */
export function Profile() {
  const { t } = useTranslation();
  const query = useProfile();
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("profile.title")}</h1>
          <p>{t("profile.subtitle")}</p>
        </div>
      </header>
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : (
        <>
          <div className="ui-two-columns">
            <div className="ui-admin-column">
              <Account data={data} />
              <Sessions data={data} />
              <Appearance />
            </div>
            <Password />
          </div>
          <TwoFactor data={data} />
          <Channels data={data} />
        </>
      )}
    </section>
  );
}

function Account({ data }: { data: ProfileData }) {
  const { t, i18n } = useTranslation();
  const { account } = data;
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("profile.account")}</h2>
      <dl className="ui-dl">
        <dt>{t("profile.name")}</dt><dd>{account.display_name || "—"}</dd>
        <dt>{t("profile.username")}</dt><dd>{account.username}</dd>
        <dt>{t("profile.email")}</dt><dd>{account.email || "—"}</dd>
        <dt>{t("profile.role")}</dt><dd>{account.role_label}</dd>
        <dt>{t("profile.lastLogin")}</dt><dd>{formatDateTime(account.last_login_at, i18n.language)}</dd>
      </dl>
    </section>
  );
}

function Sessions({ data }: { data: ProfileData }) {
  const { t } = useTranslation();
  const revoke = useProfileAction("/sessions/revoke");
  const [confirming, setConfirming] = useState(false);
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("profile.sessions")}</h2>
      <p className="ui-muted">{t("profile.sessionsHelp", { minutes: data.session_idle_minutes })}</p>
      {confirming ? (
        <div className="ui-row-actions">
          <span>{t("profile.revokeConfirm")}</span>
          <button type="button" className="ui-button" disabled={revoke.isPending} onClick={() => revoke.mutate(undefined, { onSuccess: () => setConfirming(false) })}>
            {t("profile.revoke")}
          </button>
          <button type="button" className="ui-button ui-button-ghost" onClick={() => setConfirming(false)}>{t("profile.cancel")}</button>
        </div>
      ) : (
        <button type="button" className="ui-button ui-button-ghost" onClick={() => setConfirming(true)}>{t("profile.revoke")}</button>
      )}
      {revoke.isSuccess && <small className="ui-live">{t("profile.revoked")}</small>}
    </section>
  );
}

function Appearance() {
  const { t } = useTranslation();
  const session = useSession();
  const update = useUpdatePreferences();
  const theme = session.data?.preferences.theme ?? "light";
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("profile.appearance")}</h2>
      <p className="ui-muted">{t("profile.appearanceHelp")}</p>
      <div className="ui-chips" role="group" aria-label={t("profile.appearance")}>
        {(["system", "light", "dark"] as const).map((key) => (
          <button key={key} type="button" className={`ui-chip${theme === key ? " is-active" : ""}`} aria-pressed={theme === key}
            disabled={update.isPending} onClick={() => update.mutate({ theme: key })}>
            {t(`user.${key}`)}
          </button>
        ))}
      </div>
    </section>
  );
}

function Password() {
  const { t } = useTranslation();
  const errorText = useErrorText();
  const client = useQueryClient();
  const change = useProfileAction<{ current_password: string; new_password: string; confirm_password: string }, { csrf_token: string }>("/password");
  const [form, setForm] = useState({ current_password: "", new_password: "", confirm_password: "" });
  const set = (key: keyof typeof form) => (event: { target: { value: string } }) => setForm({ ...form, [key]: event.target.value });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    change.mutate(form, {
      onSuccess: (result) => {
        // The server re-issued this session with a new CSRF token.
        setCsrfToken(result.csrf_token);
        void client.invalidateQueries({ queryKey: ["session"] });
        setForm({ current_password: "", new_password: "", confirm_password: "" });
      },
    });
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("profile.password")}</h2>
      <p className="ui-muted">{t("profile.passwordHelp")}</p>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit}>
        <label><span>{t("profile.currentPassword")}</span>
          <input type="password" value={form.current_password} onChange={set("current_password")} autoComplete="current-password" required /></label>
        <label><span>{t("profile.newPassword")}</span>
          <input type="password" value={form.new_password} onChange={set("new_password")} autoComplete="new-password" minLength={12} required />
          <small className="ui-muted">{t("profile.passwordRule")}</small></label>
        <label><span>{t("profile.confirmPassword")}</span>
          <input type="password" value={form.confirm_password} onChange={set("confirm_password")} autoComplete="new-password" minLength={12} required /></label>
        <button type="submit" className="ui-button" disabled={change.isPending}>{t("profile.changePassword")}</button>
        {change.isSuccess && <small className="ui-live">{t("profile.passwordChanged")}</small>}
        {change.error && <small className="ui-backup-error">{errorText(change.error)}</small>}
      </form>
    </section>
  );
}

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard?.writeText(codes.join("\n")).then(() => setCopied(true), () => setCopied(false));
  };
  return (
    <div className="ui-alert ui-alert-warning">
      <strong>{t("profile.codesTitle")}</strong>
      <p>{t("profile.codesHelp")}</p>
      <pre className="ui-code-block ui-recovery-codes">{codes.join("\n")}</pre>
      <div className="ui-row-actions">
        <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={copy}>{copied ? t("profile.copied") : t("profile.copy")}</button>
        <button type="button" className="ui-button ui-button-small" onClick={onDone}>{t("profile.codesSaved")}</button>
      </div>
    </div>
  );
}

function TwoFactor({ data }: { data: ProfileData }) {
  const { t, i18n } = useTranslation();
  const errorText = useErrorText();
  const state = data.two_factor;
  const setup = useProfileAction("/2fa/setup");
  const enable = useProfileAction<{ code: string }, { codes: string[] }>("/2fa/enable");
  const recovery = useProfileAction<{ password: string }, { codes: string[] }>("/2fa/recovery");
  const disable = useProfileAction<{ password: string; code: string }>("/2fa/disable");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [code, setCode] = useState("");
  const [recoveryPassword, setRecoveryPassword] = useState("");
  const [off, setOff] = useState({ password: "", code: "" });

  return (
    <section className="ui-panel ui-admin-section" id="two-factor">
      <div className="ui-page-header-row">
        <h2>{t("profile.twoFactor")}</h2>
        <span className={`ui-status ui-status-${state.enabled ? "online" : "offline"}`}>{state.enabled ? t("profile.active") : t("profile.inactive")}</span>
      </div>
      <p className="ui-muted">{t("profile.twoFactorHelp")}</p>
      {codes && <RecoveryCodes codes={codes} onDone={() => setCodes(null)} />}
      {state.enabled ? (
        <>
          <p>
            {t("profile.activeSince", { when: formatDateTime(state.since, i18n.language) })} · {t("profile.recoveryLeft", { count: state.recovery_left })}
            {state.recovery_left < 3 && <strong className="ui-warning-text"> — {t("profile.recoveryLow")}</strong>}
          </p>
          <form className="ui-admin-form" onSubmit={(event) => {
            event.preventDefault();
            recovery.mutate({ password: recoveryPassword }, { onSuccess: (result) => { setCodes(result.codes); setRecoveryPassword(""); } });
          }}>
            <label><span>{t("profile.currentPassword")}</span>
              <input type="password" value={recoveryPassword} onChange={(event) => setRecoveryPassword(event.target.value)} autoComplete="current-password" required /></label>
            <button type="submit" className="ui-button ui-button-ghost" disabled={recovery.isPending}>{t("profile.newCodes")}</button>
            {recovery.error && <small className="ui-backup-error">{errorText(recovery.error)}</small>}
          </form>
          <form className="ui-admin-form" onSubmit={(event) => {
            event.preventDefault();
            disable.mutate(off, { onSuccess: () => setOff({ password: "", code: "" }) });
          }}>
            <label><span>{t("profile.currentPassword")}</span>
              <input type="password" value={off.password} onChange={(event) => setOff({ ...off, password: event.target.value })} autoComplete="current-password" required /></label>
            <label><span>{t("profile.appCode")}</span>
              <input value={off.code} onChange={(event) => setOff({ ...off, code: event.target.value })} inputMode="numeric" autoComplete="one-time-code" maxLength={6} required /></label>
            <button type="submit" className="ui-button ui-button-danger" disabled={disable.isPending}>{t("profile.disable")}</button>
            {disable.error && <small className="ui-backup-error">{errorText(disable.error)}</small>}
          </form>
        </>
      ) : state.pending ? (
        <div className="ui-two-factor-setup">
          <img className="ui-two-factor-qr" src={state.pending.qr} alt={t("profile.qrAlt")} width={200} height={200} />
          <div>
            <p>{t("profile.step1")}</p>
            <p><code>{state.pending.secret}</code></p>
            <p>{t("profile.step2")}</p>
            <form className="ui-admin-form" onSubmit={(event) => {
              event.preventDefault();
              enable.mutate({ code }, { onSuccess: (result) => { setCodes(result.codes); setCode(""); } });
            }}>
              <label><span>{t("profile.appCode")}</span>
                <input value={code} onChange={(event) => setCode(event.target.value)} inputMode="numeric" autoComplete="one-time-code" maxLength={6} placeholder="123456" required /></label>
              <button type="submit" className="ui-button" disabled={enable.isPending}>{t("profile.enable")}</button>
              {enable.error && <small className="ui-backup-error">{errorText(enable.error)}</small>}
            </form>
          </div>
        </div>
      ) : (
        <button type="button" className="ui-button" disabled={setup.isPending} onClick={() => setup.mutate(undefined)}>{t("profile.setup")}</button>
      )}
    </section>
  );
}

type ChannelForm = { enabled: boolean; level: string; categories: string[]; chat_id: string; webhook: string };

function channelForm(channel: ChannelPreference): ChannelForm {
  return { enabled: channel.enabled, level: channel.min_severity, categories: channel.categories, chat_id: channel.destination ?? "", webhook: "" };
}

function Channels({ data }: { data: ProfileData }) {
  const { t, i18n } = useTranslation();
  const errorText = useErrorText();
  const prefs = data.notifications;
  const save = useProfileAction("/notifications");
  const test = useProfileAction<undefined, { queued: boolean }>("/notifications/test");
  const digest = useProfileAction<{ digest: string }, { digest: string }>("/notifications/digest");
  const [email, setEmail] = useState(data.account.email ?? "");
  const [channels, setChannels] = useState<Record<string, ChannelForm>>(() =>
    Object.fromEntries(Object.entries(prefs.channels).map(([key, channel]) => [key, channelForm(channel)])));
  const [period, setPeriod] = useState(prefs.digest);
  const update = (key: string, patch: Partial<ChannelForm>) => setChannels({ ...channels, [key]: { ...channels[key], ...patch } });
  const toggleCategory = (key: string, category: string) => {
    const current = channels[key].categories;
    update(key, { categories: current.includes(category) ? current.filter((item) => item !== category) : [...current, category] });
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate({ email, channels }, { onSuccess: () => setChannels(Object.fromEntries(Object.entries(channels).map(([key, form]) => [key, { ...form, webhook: "" }]))) });
  };

  return (
    <section className="ui-panel ui-admin-section" id="notifications">
      <h2>{t("profile.notifications")}</h2>
      <p className="ui-muted">{t("profile.notificationsHelp")}</p>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit}>
        <label><span>{t("profile.yourEmail")}</span>
          <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="nome@example.net" autoComplete="email" />
          <small className="ui-muted">{t("profile.yourEmailHelp")}</small></label>
        <div className="ui-channel-grid">
          {Object.entries(prefs.channels).map(([key, channel]) => {
            const form = channels[key];
            const label = t(`notificationsAdmin.channels.${key}`, { defaultValue: key });
            return (
              <fieldset key={key} className="ui-channel">
                <legend>{label}</legend>
                {!channel.configured && <small className="ui-warning-text">{t("profile.notConfigured")}</small>}
                <label className="ui-with-brand"><input type="checkbox" checked={form.enabled} onChange={(event) => update(key, { enabled: event.target.checked })} />
                  <span>{t("profile.receiveOn", { channel: label })}</span></label>
                {key === "telegram" && (
                  <label><span>{t("profile.chatId")}</span>
                    <input value={form.chat_id} onChange={(event) => update(key, { chat_id: event.target.value })} placeholder="123456789" inputMode="numeric" />
                    <small className="ui-muted">{t("profile.chatIdHelp")}</small></label>
                )}
                {key === "slack" && (
                  <label><span>{t("profile.webhook")}</span>
                    <input type="password" value={form.webhook} onChange={(event) => update(key, { webhook: event.target.value })} autoComplete="off"
                      placeholder={channel.has_secret ? t("notificationsAdmin.keepSecret") : "https://hooks.slack.com/services/…"} />
                    <small className="ui-muted">{t("profile.webhookHelp")}</small></label>
                )}
                <label><span>{t("profile.minLevel")}</span>
                  <select value={form.level} onChange={(event) => update(key, { level: event.target.value })}>
                    {prefs.levels.map((level) => <option key={level} value={level}>{t(`notificationsAdmin.levels.${level}`, { defaultValue: level })}</option>)}
                  </select></label>
                <div className="ui-check-grid" role="group" aria-label={t("profile.categories")}>
                  {prefs.categories.map((category) => (
                    <label key={category} className="ui-with-brand">
                      <input type="checkbox" checked={form.categories.includes(category)} onChange={() => toggleCategory(key, category)} />
                      <span>{t(`notificationsAdmin.categories.${category}`, { defaultValue: category })}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
            );
          })}
        </div>
        <small className="ui-muted">{t("profile.categoriesHelp")}</small>
        <button type="submit" className="ui-button" disabled={save.isPending}>{t("profile.savePreferences")}</button>
        {save.isSuccess && <small className="ui-live">{t("profile.preferencesSaved")}</small>}
        {save.error && <small className="ui-backup-error">{errorText(save.error)}</small>}
      </form>
      <form className="ui-admin-form" onSubmit={(event) => { event.preventDefault(); digest.mutate({ digest: period }); }}>
        <label><span>{t("profile.digest")}</span>
          <select value={period} onChange={(event) => setPeriod(event.target.value)}>
            {prefs.digest_options.map((option) => <option key={option} value={option}>{t(`profile.digestOptions.${option}`, { defaultValue: option })}</option>)}
          </select></label>
        <button type="submit" className="ui-button ui-button-ghost" disabled={digest.isPending}>{t("profile.saveDigest")}</button>
        {digest.isSuccess && <small className="ui-live">{t("profile.digestSaved")}</small>}
      </form>
      <p className="ui-muted">{t("profile.digestHelp")}</p>
      <div className="ui-row-actions">
        <button type="button" className="ui-button ui-button-ghost" disabled={test.isPending} onClick={() => test.mutate(undefined)}>{t("profile.sendTest")}</button>
        {test.data && <small className={test.data.queued ? "ui-live" : "ui-warning-text"}>{t(test.data.queued ? "profile.testQueued" : "profile.testNone")}</small>}
      </div>
      {prefs.recent.length > 0 && (
        <details>
          <summary>{t("profile.recent")}</summary>
          <div className="ui-data-wrap">
            <table className="ui-data-table">
              <thead><tr>{["when", "channel", "message", "status"].map((key) => <th key={key}><span className="ui-th">{t(`profile.recentColumns.${key}`)}</span></th>)}</tr></thead>
              <tbody>
                {prefs.recent.map((delivery) => (
                  <tr key={delivery.id}>
                    <td className="ui-nowrap">{formatDateTime(delivery.created_at, i18n.language)}</td>
                    <td>{t(`notificationsAdmin.channels.${delivery.channel}`, { defaultValue: delivery.channel })}</td>
                    <td>{delivery.subject}</td>
                    <td>
                      <span className={`ui-status ui-status-${DELIVERY_TONE[delivery.status] ?? "warning"}`}>{t(`profile.deliveryStatus.${delivery.status}`, { defaultValue: delivery.status })}</span>
                      {delivery.last_error && <small className="ui-block ui-warning-text">{delivery.last_error}</small>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </section>
  );
}
