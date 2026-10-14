import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { type ApiKeyRow, type ApiKeys, type NewToken, useApiKeyAction, useApiKeys } from "../api/admin";
import { AdminTabs } from "../app/AdminTabs";
import { formatDateTime } from "../lib/format";

const STATE_TONE: Record<string, string> = { active: "online", grace: "warning", expiring: "warning", expired: "offline", replaced: "offline", revoked: "offline" };

export function AdminApiKeys() {
  const { t } = useTranslation();
  const query = useApiKeys();
  const [token, setToken] = useState<NewToken | null>(null);
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("admin.title")}</h1>
          <p>{t("apiKeys.subtitle")}</p>
        </div>
        <a className="ui-button ui-button-ghost" href="/admin/api-keys">{t("app.classic")}</a>
      </header>
      <AdminTabs />
      {token && <TokenNotice token={token} onDone={() => setToken(null)} />}
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <div className="ui-empty">{t("app.loading")}</div>
      ) : (
        <>
          <Review data={data} />
          <CreateKey data={data} onToken={setToken} />
          <section className="ui-panel ui-admin-section">
            <h2>{t("apiKeys.keys", { count: data.keys.length })}</h2>
            {data.keys.length ? (
              <ul className="ui-admin-list">
                {data.keys.map((key) => <KeyItem key={key.id} item={key} data={data} onToken={setToken} />)}
              </ul>
            ) : (
              <div className="ui-empty">{t("apiKeys.none")}</div>
            )}
          </section>
        </>
      )}
    </section>
  );
}

function TokenNotice({ token, onDone }: { token: NewToken; onDone: () => void }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copy = () => void navigator.clipboard?.writeText(token.token).then(() => setCopied(true));
  return (
    <div className="ui-alert ui-alert-warning ui-token-notice" role="alert">
      <strong>{t("apiKeys.tokenTitle", { name: token.name })}</strong>
      <p>{t("apiKeys.tokenOnce")}</p>
      <div className="ui-token-row">
        <input readOnly value={token.token} aria-label={t("apiKeys.token")} onFocus={(event) => event.target.select()} spellCheck={false} />
        <button type="button" className="ui-button ui-button-small" onClick={copy}>{copied ? t("apiKeys.copied") : t("apiKeys.copy")}</button>
        <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={onDone}>{t("apiKeys.stored")}</button>
      </div>
    </div>
  );
}

function Review({ data }: { data: ApiKeys }) {
  const { t } = useTranslation();
  const items = (["expired", "expiring", "unused", "no_expiry"] as const).filter((key) => data.review[key] > 0);
  if (!items.length) return null;
  return (
    <div className="ui-alert ui-alert-warning">
      {items.map((key) => t(`apiKeys.review.${key}`, { count: data.review[key] })).join(" · ")}
    </div>
  );
}

function CreateKey({ data, onToken }: { data: ApiKeys; onToken: (token: NewToken) => void }) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [customer, setCustomer] = useState("");
  const [days, setDays] = useState(90);
  const [scopes, setScopes] = useState<Set<string>>(new Set());
  const create = useApiKeyAction<object, NewToken>("");
  const flip = (scope: string) => setScopes((previous) => {
    const next = new Set(previous);
    if (next.has(scope)) next.delete(scope);
    else next.add(scope);
    return next;
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate({ name, customer_id: customer, expires_days: days, scopes: [...scopes] }, {
      onSuccess: (token) => { onToken(token); setName(""); setScopes(new Set()); },
    });
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("apiKeys.create")}</h2>
      <p className="ui-muted">{t("apiKeys.createHelp")}</p>
      <form className="ui-admin-form" onSubmit={submit}>
        <label><span>{t("apiKeys.name")}</span><input value={name} onChange={(event) => setName(event.target.value)} maxLength={160} required /></label>
        <label>
          <span>{t("apiKeys.customer")}</span>
          <select value={customer} onChange={(event) => setCustomer(event.target.value)}>
            <option value="">{t("reports.allCustomers")}</option>
            {data.customers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>
        <label>
          <span>{t("apiKeys.expiry")}</span>
          <select value={days} onChange={(event) => setDays(Number(event.target.value))}>
            {data.expiry_days.map((value) => <option key={value} value={value}>{value ? t("apiKeys.days", { count: value }) : t("apiKeys.noExpiry")}</option>)}
          </select>
        </label>
        <fieldset className="ui-admin-wide ui-scope-set">
          <legend>{t("apiKeys.scopes")}</legend>
          {data.scopes.map((scope, index) => (
            <label key={scope} className="ui-with-brand">
              <input type="checkbox" checked={index === 0 || scopes.has(scope)} disabled={index === 0} onChange={() => flip(scope)} />
              <span>{t(`apiKeys.scopeLabels.${scope}`, { defaultValue: scope })} <small className="ui-muted">{t(`apiKeys.scopeHelp.${scope}`, { defaultValue: "" })}</small></span>
            </label>
          ))}
        </fieldset>
        <button type="submit" className="ui-button" disabled={create.isPending || !name.trim()}>{t("apiKeys.createAction")}</button>
        {create.error && <small className="ui-backup-error">{(create.error as Error).message}</small>}
      </form>
    </section>
  );
}

function KeyItem({ item, data, onToken }: { item: ApiKeyRow; data: ApiKeys; onToken: (token: NewToken) => void }) {
  const { t, i18n } = useTranslation();
  const [grace, setGrace] = useState(data.grace_hours.includes(24) ? 24 : data.grace_hours[0]);
  const rotate = useApiKeyAction<object, NewToken>(`/${item.id}/rotate`);
  const revoke = useApiKeyAction<object>(`/${item.id}/revoke`);
  const remove = useApiKeyAction<object>(`/${item.id}/delete`);
  const error = rotate.error ?? revoke.error ?? remove.error;
  const hints = item.hints;
  const when = (value: string | null) => (value ? formatDateTime(value, i18n.language) : t("adminUsers.never"));
  return (
    <li className={item.active ? "" : "is-muted"}>
      <div>
        <strong>{item.name}</strong>{" "}
        <span className={`ui-status ui-status-${STATE_TONE[item.state] ?? "unknown"}`}>{t(`apiKeys.states.${item.state}`)}</span>
        <small className="ui-muted ui-block"><code>{item.prefix}…</code> · {item.scopes.map((scope) => t(`apiKeys.scopeLabels.${scope}`, { defaultValue: scope })).join(", ")} · {item.customer?.name ?? t("reports.allCustomers")}</small>
        <small className="ui-muted ui-block">
          {t("apiKeys.created", { when: when(item.created_at) })} · {item.expires_at ? t("apiKeys.expires", { when: when(item.expires_at) }) : t("apiKeys.noExpiry")}
        </small>
        <small className="ui-muted ui-block">
          {t("apiKeys.lastUsed", { when: when(item.last_used_at) })}{item.last_used_ip && ` · ${item.last_used_ip}`} · {t("apiKeys.uses", { count: item.use_count })}
        </small>
        {(hints.grace_until || hints.expires_in_days !== undefined || hints.never_used_days || hints.idle_days || hints.no_expiry) && (
          <small className="ui-block ui-key-hint">
            {[
              hints.grace_until && t("apiKeys.hints.grace", { when: when(hints.grace_until) }),
              hints.expires_in_days !== undefined && t("apiKeys.hints.expiring", { count: hints.expires_in_days }),
              hints.never_used_days && t("apiKeys.hints.neverUsed", { count: hints.never_used_days }),
              hints.idle_days && t("apiKeys.hints.idle", { count: hints.idle_days }),
              hints.no_expiry && t("apiKeys.hints.noExpiry"),
            ].filter(Boolean).join(" ")}
          </small>
        )}
        {error && <small className="ui-backup-error ui-block">{(error as Error).message}</small>}
      </div>
      <div className="ui-admin-actions">
        {item.active && !item.replaced_by_id && (
          <div className="ui-row-actions">
            <select value={grace} onChange={(event) => setGrace(Number(event.target.value))} aria-label={t("apiKeys.grace")}>
              {data.grace_hours.map((hours) => <option key={hours} value={hours}>{t(`apiKeys.graceOptions.h${hours}`, { defaultValue: `${hours} h` })}</option>)}
            </select>
            <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={rotate.isPending}
              onClick={() => rotate.mutate({ grace_hours: grace }, { onSuccess: onToken })}>
              {t("apiKeys.rotate")}
            </button>
          </div>
        )}
        <div className="ui-row-actions">
          {item.active ? (
            <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={revoke.isPending}
              onClick={() => { if (window.confirm(t("apiKeys.confirmRevoke", { name: item.name }))) revoke.mutate({}); }}>
              {t("apiKeys.revoke")}
            </button>
          ) : (
            <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={remove.isPending}
              onClick={() => { if (window.confirm(t("apiKeys.confirmDelete", { name: item.name }))) remove.mutate({}); }}>
              {t("apiKeys.delete")}
            </button>
          )}
        </div>
      </div>
    </li>
  );
}
