import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { AdminTabs } from "../app/AdminTabs";
import { BrandIcon, type BrandRef } from "../app/BrandIcon";
import { IntegrationTabs } from "../app/IntegrationTabs";
import { Loading } from "../app/States";
import { formatDateTime, formatRelative } from "../lib/format";

interface Quota { remaining: number; limit: number | null; reset_at?: string; seen_at: string }
interface Category { id: number; name: string }
interface Reported {
  address: string; score: number; reports: number; last: string | null; country: string | null; block?: string;
  categories: Category[]; device: { id: string; name: string; brand: BrandRef } | null; customer: string | null; unassigned: boolean; over: boolean;
}
interface Settings { max_age_days: number; threshold: number; recheck_hours: number; reserve_checks: number; reserve_blocks: number }
interface AbuseIpdb extends Settings {
  enabled: boolean; has_key: boolean; quota: Record<string, Quota>; retry_until: string | null; last_error: { at: string; message: string } | null;
  evaluated_at: string | null; prefixes: string[]; coverage: { blocks: number; addresses: number; checked: number }; reported: Reported[];
}
interface Lookup { address: string; score: number; reports: number; reporters: number; last: string | null; country: string | null; isp: string | null; usage: string | null; categories: Category[]; comments: string[] }

const URL = "/api/ui/v1/admin/integrations/abuseipdb";

/** AbuseIPDB (ABUSEIPDB-01): reputation of the operator's prefixes and public addresses, within the free plan's quota. */
export function AdminAbuseIpdb() {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["admin-abuseipdb"], queryFn: () => api<AbuseIpdb>(URL) });
  const set = (data: AbuseIpdb) => client.setQueryData(["admin-abuseipdb"], data);
  const test = useMutation({ mutationFn: () => api<AbuseIpdb & { ok: boolean; message: string | null }>(`${URL}/test`, { method: "POST" }), onSuccess: set });
  const run = useMutation({ mutationFn: () => api<AbuseIpdb & { stats: { calls?: number; due?: number } }>(`${URL}/run`, { method: "POST" }), onSuccess: set });
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("abuseipdb.title")}</h1>
          <p>{t("abuseipdb.subtitle")}</p>
        </div>
        {data?.has_key && (
          <div className="ui-row-actions">
            <button type="button" className="ui-button ui-button-ghost" disabled={test.isPending} onClick={() => test.mutate()}>{t("abuseipdb.test")}</button>
            {data.enabled && <button type="button" className="ui-button" disabled={run.isPending} onClick={() => run.mutate()}>{run.isPending ? t("app.loading") : t("abuseipdb.run")}</button>}
          </div>
        )}
      </header>
      <AdminTabs />
      <IntegrationTabs />
      {test.data && <div className={`ui-alert ${test.data.ok ? "ui-alert-success" : "ui-alert-danger"}`}>{test.data.ok ? t("abuseipdb.testOk") : test.data.message}</div>}
      {run.data && <div className="ui-alert ui-alert-success">{t("abuseipdb.ran", { calls: run.data.stats.calls ?? 0, due: run.data.stats.due ?? 0 })}</div>}
      {(test.error || run.error) && <div className="ui-alert ui-alert-danger">{((test.error || run.error) as Error).message}</div>}
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : (
        <>
          <div className="ui-two-columns">
            <SettingsForm data={data} onSaved={set} />
            <section className="ui-panel ui-admin-section">
              <h2>{t("abuseipdb.statusTitle")}</h2>
              {data.last_error && <div className="ui-alert ui-alert-warning">{data.last_error.message}</div>}
              {data.retry_until && new Date(data.retry_until) > new Date() && <div className="ui-alert ui-alert-warning">{t("abuseipdb.waiting", { when: formatDateTime(data.retry_until, i18n.language) })}</div>}
              <ul className="ui-cap-list">
                {(["check", "check-block"] as const).map((endpoint) => {
                  const quota = data.quota[endpoint];
                  return (
                    <li key={endpoint}>
                      <div>
                        <strong>{t(`abuseipdb.endpoint.${endpoint}`)}</strong>
                        <small className="ui-muted ui-block">
                          {quota ? t("abuseipdb.quota", { remaining: quota.remaining, limit: quota.limit ?? "?", when: quota.reset_at ? formatRelative(quota.reset_at, i18n.language) : "—" }) : t("abuseipdb.quotaUnknown")}
                        </small>
                      </div>
                    </li>
                  );
                })}
              </ul>
              <p className="ui-muted">{t("abuseipdb.coverage", data.coverage)}</p>
              <p className="ui-muted">{data.prefixes.length ? t("abuseipdb.prefixes", { list: data.prefixes.join(", ") }) : t("abuseipdb.noPrefixes")}{" "}
                <Link to="/admin/integrations/threat-lists">{t("abuseipdb.prefixesLink")}</Link></p>
              {data.evaluated_at && <p className="ui-muted">{t("abuseipdb.evaluated", { when: formatDateTime(data.evaluated_at, i18n.language) })}</p>}
            </section>
          </div>
          {data.has_key && <LookupForm />}
          <section className="ui-panel ui-admin-section">
            <h2>{t("abuseipdb.reportedTitle")}</h2>
            <p className="ui-muted">{t("abuseipdb.reportedHelp", { threshold: data.threshold })}</p>
            {data.reported.length === 0 ? (
              <div className="ui-empty">{t("abuseipdb.noReported")}</div>
            ) : (
              <div className="ui-data-wrap">
                <table className="ui-data-table">
                  <thead><tr>{["address", "score", "holder", "categories", "last"].map((key) => <th key={key}><span className="ui-th">{t(`abuseipdb.columns.${key}`)}</span></th>)}</tr></thead>
                  <tbody>
                    {data.reported.map((row) => (
                      <tr key={row.address}>
                        <td className="ui-mono">{row.address}{row.block && <small className="ui-muted ui-block">{row.block}</small>}</td>
                        <td><span className={row.over ? "ui-severity ui-severity-warning" : "ui-severity"}>{row.score}%</span>
                          <small className="ui-muted ui-block">{t("abuseipdb.reports", { count: row.reports })}</small></td>
                        <td>
                          {row.device ? (
                            <span className="ui-with-brand"><BrandIcon brand={row.device.brand} small />
                              <Link to="/devices/$deviceId" params={{ deviceId: row.device.id }}>{row.device.name}</Link>
                              {row.customer && <small className="ui-muted"> · {row.customer}</small>}</span>
                          ) : row.unassigned ? t("abuseipdb.unassigned") : t("abuseipdb.unknownHolder")}
                        </td>
                        <td>{row.categories.map((c) => c.name).join(", ") || "—"}</td>
                        <td>{row.last ? formatRelative(row.last, i18n.language) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </section>
  );
}

function SettingsForm({ data, onSaved }: { data: AbuseIpdb; onSaved: (data: AbuseIpdb) => void }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({ enabled: data.enabled, api_key: "", max_age_days: data.max_age_days, threshold: data.threshold,
    recheck_hours: data.recheck_hours, reserve_checks: data.reserve_checks, reserve_blocks: data.reserve_blocks });
  const save = useMutation({
    mutationFn: (body: typeof form) => api<AbuseIpdb>(URL, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: (saved) => { setForm((current) => ({ ...current, api_key: "" })); onSaved(saved); },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(form);
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("abuseipdb.settingsTitle")}</h2>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit} autoComplete="off">
        <label className="ui-with-brand"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /> <span>{t("abuseipdb.enabled")}</span></label>
        <label>
          <span>{t("abuseipdb.apiKey")}</span>
          <input type="password" value={form.api_key} placeholder={data.has_key ? t("abuseipdb.keySaved") : ""} autoComplete="new-password"
            onChange={(event) => setForm({ ...form, api_key: event.target.value })} />
        </label>
        <small className="ui-muted">{t("abuseipdb.keyHelp")}</small>
        {(["threshold", "max_age_days", "recheck_hours", "reserve_checks", "reserve_blocks"] as const).map((key) => (
          <label key={key}>
            <span>{t(`abuseipdb.fields.${key}`)}</span>
            <input type="number" value={form[key]} onChange={(event) => setForm({ ...form, [key]: Number(event.target.value) })} />
          </label>
        ))}
        <button type="submit" className="ui-button" disabled={save.isPending}>{t("cnmaestro.save")}</button>
        {save.isSuccess && <small className="ui-live">{t("abuseipdb.saved")}</small>}
        {save.error && <small className="ui-backup-error">{(save.error as Error).message}</small>}
      </form>
    </section>
  );
}

function LookupForm() {
  const { t, i18n } = useTranslation();
  const [address, setAddress] = useState("");
  const lookup = useMutation({ mutationFn: () => api<Lookup>(`${URL}/lookup`, { method: "POST", body: JSON.stringify({ address }) }) });
  const result = lookup.data;
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("abuseipdb.lookupTitle")}</h2>
      <form className="ui-admin-form ui-row-actions" onSubmit={(event) => { event.preventDefault(); lookup.mutate(); }} autoComplete="off">
        <input value={address} onChange={(event) => setAddress(event.target.value)} placeholder="203.0.113.10" inputMode="decimal" aria-label={t("abuseipdb.lookupTitle")} />
        <button type="submit" className="ui-button ui-button-ghost" disabled={lookup.isPending || !address.trim()}>{t("abuseipdb.lookup")}</button>
      </form>
      {lookup.error && <small className="ui-backup-error">{(lookup.error as Error).message}</small>}
      {result && (
        <div className="ui-admin-stack">
          <p><strong className="ui-mono">{result.address}</strong> · {t("abuseipdb.score", { score: result.score })} · {t("abuseipdb.reports", { count: result.reports })}
            {result.last ? ` · ${formatRelative(result.last, i18n.language)}` : ""}</p>
          <p className="ui-muted">{[result.isp, result.usage, result.country].filter(Boolean).join(" · ")}</p>
          {result.categories.length > 0 && <p>{result.categories.map((c) => c.name).join(", ")}</p>}
          {result.comments.length > 0 && <ul>{result.comments.map((comment, index) => <li key={index} className="ui-muted">{comment}</li>)}</ul>}
        </div>
      )}
    </section>
  );
}
