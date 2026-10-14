import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { type AdminUser, type AdminUsers as Data, useAdminUsers, useUserAction } from "../api/admin";
import { AdminTabs } from "../app/AdminTabs";
import { formatDateTime } from "../lib/format";

export function AdminUsers() {
  const { t } = useTranslation();
  const query = useAdminUsers();
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("admin.title")}</h1>
          <p>{t("admin.subtitle")}</p>
        </div>
        <a className="ui-button ui-button-ghost" href="/admin/users">{t("app.classic")}</a>
      </header>
      <AdminTabs />
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <div className="ui-empty">{t("app.loading")}</div>
      ) : (
        <>
          <CreateUser data={data} />
          <section className="ui-panel ui-admin-section">
            <h2>{t("adminUsers.accounts", { count: data.users.length })}</h2>
            <ul className="ui-admin-list">
              {data.users.map((user) => <UserItem key={user.id} user={user} data={data} />)}
            </ul>
          </section>
          <Failures data={data} />
          <section className="ui-panel ui-admin-section">
            <h2>{t("adminUsers.rolesTitle")}</h2>
            <dl className="ui-dl">
              {data.roles.map((role) => (
                <div key={role}><dt>{t(`adminUsers.roles.${role}`, { defaultValue: role })}</dt><dd>{t(`adminUsers.roleHelp.${role}`, { defaultValue: "" })}</dd></div>
              ))}
            </dl>
          </section>
        </>
      )}
    </section>
  );
}

function CreateUser({ data }: { data: Data }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(data.users.length < 2);
  const [form, setForm] = useState({ username: "", display_name: "", email: "", role: data.roles.includes("operator") ? "operator" : data.roles[0], password: "" });
  const create = useUserAction<typeof form>("");
  const set = (key: keyof typeof form) => (event: { target: { value: string } }) => setForm({ ...form, [key]: event.target.value });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate(form, { onSuccess: () => setForm({ ...form, username: "", display_name: "", email: "", password: "" }) });
  };
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("adminUsers.newUser")}</h2>
        {!open && <button type="button" className="ui-button" onClick={() => setOpen(true)}>{t("adminUsers.newUser")}</button>}
      </div>
      {open && (
        <form className="ui-admin-form" onSubmit={submit} autoComplete="off">
          <label><span>{t("adminUsers.username")}</span><input value={form.username} onChange={set("username")} required autoComplete="off" /></label>
          <label><span>{t("adminUsers.displayName")}</span><input value={form.display_name} onChange={set("display_name")} /></label>
          <label><span>{t("adminUsers.email")}</span><input type="email" value={form.email} onChange={set("email")} /></label>
          <label>
            <span>{t("adminUsers.role")}</span>
            <select value={form.role} onChange={set("role")}>
              {data.roles.map((role) => <option key={role} value={role}>{t(`adminUsers.roles.${role}`, { defaultValue: role })}</option>)}
            </select>
          </label>
          <label>
            <span>{t("adminUsers.password")}</span>
            <input type="password" value={form.password} onChange={set("password")} required minLength={12} autoComplete="new-password" />
          </label>
          <button type="submit" className="ui-button" disabled={create.isPending}>{t("adminUsers.create")}</button>
          <small className="ui-muted">{t("adminUsers.passwordHint")}</small>
          {create.isSuccess && <small className="ui-live">{t("adminUsers.created")}</small>}
          {create.error && <small className="ui-backup-error">{(create.error as Error).message}</small>}
        </form>
      )}
    </section>
  );
}

function UserItem({ user, data }: { user: AdminUser; data: Data }) {
  const { t, i18n } = useTranslation();
  const [editingScope, setEditingScope] = useState(false);
  const [chosen, setChosen] = useState<Set<string>>(new Set(user.scope.map((item) => item.id)));
  const role = useUserAction<{ role: string }>(`/${user.id}/role`);
  const toggle = useUserAction<object>(`/${user.id}/toggle`);
  const scope = useUserAction<{ customer_ids: string[] }>(`/${user.id}/scope`);
  const sessions = useUserAction<object>(`/${user.id}/sessions/revoke`);
  const reset = useUserAction<object>(`/${user.id}/2fa/reset`);
  const error = role.error ?? toggle.error ?? scope.error ?? sessions.error ?? reset.error;
  const flip = (id: string) => setChosen((previous) => {
    const next = new Set(previous);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });
  return (
    <li className={user.active ? "" : "is-muted"}>
      <div>
        <strong>{user.display_name || user.username}</strong>{" "}
        <span className={`ui-status ui-status-${user.active ? "online" : "offline"}`}>{user.active ? t("adminUsers.active") : t("adminUsers.disabled")}</span>
        {user.is_self && <small className="ui-muted"> · {t("adminUsers.you")}</small>}
        <small className="ui-muted ui-block">{user.username}{user.email && ` · ${user.email}`}</small>
        <small className="ui-muted ui-block">
          {t("adminUsers.lastLogin", { when: user.last_login_at ? formatDateTime(user.last_login_at, i18n.language) : t("adminUsers.never") })}
          {" · "}{user.two_factor ? t("adminUsers.twoFactorOn") : t("adminUsers.twoFactorOff")}
        </small>
        <small className="ui-muted ui-block">
          {user.role === "admin"
            ? t("adminUsers.scopeAdmin")
            : user.scope.length ? t("adminUsers.scopeSome", { names: user.scope.map((item) => item.name).join(", ") }) : t("adminUsers.scopeAll")}
        </small>
        {editingScope && (
          <form onSubmit={(event) => { event.preventDefault(); scope.mutate({ customer_ids: [...chosen] }, { onSuccess: () => setEditingScope(false) }); }}>
            <ul className="ui-incident-picker">
              {data.customers.map((customer) => (
                <li key={customer.id}>
                  <label className="ui-with-brand"><input type="checkbox" checked={chosen.has(customer.id)} onChange={() => flip(customer.id)} /> <span>{customer.name}</span></label>
                </li>
              ))}
            </ul>
            <small className="ui-muted ui-block">{t("adminUsers.scopeHelp")}</small>
            <div className="ui-row-actions">
              <button type="submit" className="ui-button ui-button-small" disabled={scope.isPending}>{t("adminUsers.saveScope")}</button>
              <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={() => setEditingScope(false)}>{t("incidentDetail.cancel")}</button>
            </div>
          </form>
        )}
        {error && <small className="ui-backup-error ui-block">{(error as Error).message}</small>}
      </div>
      <div className="ui-admin-actions">
        <select value={user.role} disabled={role.isPending} aria-label={t("adminUsers.roleOf", { name: user.username })}
          onChange={(event) => role.mutate({ role: event.target.value })}>
          {data.roles.map((key) => <option key={key} value={key}>{t(`adminUsers.roles.${key}`, { defaultValue: key })}</option>)}
        </select>
        <div className="ui-row-actions">
          {user.role !== "admin" && !editingScope && (
            <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={() => setEditingScope(true)}>{t("adminUsers.editScope")}</button>
          )}
          {!user.is_self && (
            <>
              <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={toggle.isPending} onClick={() => toggle.mutate({})}>
                {user.active ? t("adminUsers.disable") : t("adminUsers.enable")}
              </button>
              {user.active && (
                <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={sessions.isPending}
                  onClick={() => { if (window.confirm(t("adminUsers.confirmSessions", { name: user.username }))) sessions.mutate({}); }}>
                  {t("adminUsers.signOut")}
                </button>
              )}
              {user.two_factor && (
                <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={reset.isPending}
                  onClick={() => { if (window.confirm(t("adminUsers.confirmReset", { name: user.username }))) reset.mutate({}); }}>
                  {t("adminUsers.resetTwoFactor")}
                </button>
              )}
            </>
          )}
          {user.is_self && <a className="ui-button ui-button-ghost ui-button-small" href="/profile">{t("adminUsers.profile")}</a>}
        </div>
        {sessions.isSuccess && <small className="ui-live">{t("adminUsers.signedOut")}</small>}
        {reset.isSuccess && <small className="ui-live">{t("adminUsers.twoFactorReset")}</small>}
      </div>
    </li>
  );
}

function Failures({ data }: { data: Data }) {
  const { t, i18n } = useTranslation();
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("adminUsers.failures")}</h2>
      <p className="ui-muted">{t("adminUsers.failuresHelp", { pair: data.limits.pair, ip: data.limits.ip })}</p>
      {data.login_failures.length ? (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead>
              <tr>{["username", "address", "attempts", "last", "state"].map((column) => <th key={column}><span className="ui-th">{t(`adminUsers.failureColumns.${column}`)}</span></th>)}</tr>
            </thead>
            <tbody>
              {data.login_failures.map((row) => (
                <tr key={`${row.username}|${row.ip}`}>
                  <td><strong>{row.username}</strong></td>
                  <td><code>{row.ip}</code></td>
                  <td>{row.count}</td>
                  <td className="ui-nowrap">{formatDateTime(row.last, i18n.language)}</td>
                  <td>{row.blocked_minutes ? <span className="ui-severity ui-severity-high">{t("adminUsers.blocked", { minutes: row.blocked_minutes })}</span> : <span className="ui-muted">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ui-empty">{t("adminUsers.noFailures")}</div>
      )}
    </section>
  );
}
