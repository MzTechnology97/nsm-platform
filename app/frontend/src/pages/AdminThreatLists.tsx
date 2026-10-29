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

interface Source { key: string; label: string; url: string; enabled: boolean; at?: string; count?: number; error?: string | null; failed_at?: string }
interface Match {
  kind: "operator_prefix" | "device";
  address: string;
  network: string;
  reference: string;
  source: string;
  message: string;
  device: { id: string; name: string; brand: BrandRef } | null;
}
interface Attacker { ip: string; attempts: number; devices: number; last: string | null; network: string; reference: string; source: string }
interface ThreatLists { enabled: boolean; operator_prefixes: string[]; checked_at: string | null; sources: Source[]; matches: Match[]; attackers: Attacker[] }

const URL = "/api/ui/v1/admin/integrations/threat-lists";

/** Threat lists (SEC-13, SIEM-02): block lists checked against the operator's prefixes, the inventory and the login sources. */
export function AdminThreatLists() {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["admin-threat-lists"], queryFn: () => api<ThreatLists>(URL) });
  const set = (data: ThreatLists) => client.setQueryData(["admin-threat-lists"], data);
  const refresh = useMutation({ mutationFn: () => api<ThreatLists>(`${URL}/refresh`, { method: "POST" }), onSuccess: set });
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("threatLists.title")}</h1>
          <p>{t("threatLists.subtitle")}</p>
        </div>
        {data?.enabled && (
          <button type="button" className="ui-button" disabled={refresh.isPending} onClick={() => refresh.mutate()}>
            {refresh.isPending ? t("app.loading") : t("threatLists.refresh")}
          </button>
        )}
      </header>
      <AdminTabs />
      <IntegrationTabs />
      {refresh.error && <div className="ui-alert ui-alert-danger">{(refresh.error as Error).message}</div>}
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : (
        <>
          <div className="ui-two-columns">
            <Settings data={data} onSaved={set} />
            <section className="ui-panel ui-admin-section">
              <h2>{t("threatLists.sourcesTitle")}</h2>
              <ul className="ui-cap-list">
                {data.sources.map((source) => (
                  <li key={source.key}>
                    <div>
                      <strong>{source.label}</strong>
                      <small className="ui-muted ui-block">
                        {source.at ? t("threatLists.loaded", { count: source.count ?? 0, when: formatRelative(source.at, i18n.language) }) : t("threatLists.never")}
                      </small>
                      {source.error && <small className="ui-backup-error ui-block">{t("threatLists.failed", { error: source.error })}</small>}
                    </div>
                    <span className={source.enabled ? "ui-severity" : "ui-severity ui-severity-warning"}>{source.enabled ? t("threatLists.on") : t("threatLists.off")}</span>
                  </li>
                ))}
              </ul>
              <p className="ui-muted">{data.checked_at ? t("threatLists.checked", { when: formatDateTime(data.checked_at, i18n.language) }) : t("threatLists.notChecked")}</p>
            </section>
          </div>
          <section className="ui-panel ui-admin-section">
            <h2>{t("threatLists.matchesTitle")}</h2>
            <p className="ui-muted">{t("threatLists.matchesHelp")}</p>
            {data.matches.length === 0 ? (
              <div className="ui-empty">{t("threatLists.noMatches")}</div>
            ) : (
              <div className="ui-data-wrap">
                <table className="ui-data-table">
                  <thead><tr>{["what", "address", "network", "source"].map((key) => <th key={key}><span className="ui-th">{t(`threatLists.columns.${key}`)}</span></th>)}</tr></thead>
                  <tbody>
                    {data.matches.map((match) => (
                      <tr key={`${match.kind}-${match.address}-${match.network}`}>
                        <td>
                          {match.device ? (
                            <span className="ui-with-brand"><BrandIcon brand={match.device.brand} small />
                              <Link to="/devices/$deviceId" params={{ deviceId: match.device.id }}>{match.device.name}</Link></span>
                          ) : t("threatLists.operatorPrefix")}
                        </td>
                        <td className="ui-mono">{match.address}</td>
                        <td className="ui-mono">{match.network}{match.reference && <small className="ui-muted ui-block">{match.reference}</small>}</td>
                        <td>{match.source}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          <section className="ui-panel ui-admin-section">
            <h2>{t("threatLists.attackersTitle")}</h2>
            <p className="ui-muted">{t("threatLists.attackersHelp")}</p>
            {data.attackers.length === 0 ? (
              <div className="ui-empty">{t("threatLists.noAttackers")}</div>
            ) : (
              <div className="ui-data-wrap">
                <table className="ui-data-table">
                  <thead><tr>{["ip", "attempts", "network", "last"].map((key) => <th key={key}><span className="ui-th">{t(`threatLists.attackerColumns.${key}`)}</span></th>)}</tr></thead>
                  <tbody>
                    {data.attackers.map((row) => (
                      <tr key={row.ip}>
                        <td className="ui-mono">{row.ip}</td>
                        <td>{t("threatLists.attempts", { count: row.attempts, devices: row.devices })}</td>
                        <td className="ui-mono">{row.network}<small className="ui-muted ui-block">{row.source}{row.reference ? ` · ${row.reference}` : ""}</small></td>
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

function Settings({ data, onSaved }: { data: ThreatLists; onSaved: (data: ThreatLists) => void }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({
    enabled: data.enabled, operator_prefixes: data.operator_prefixes.join("\n"),
    sources: Object.fromEntries(data.sources.map((s) => [s.key, { url: s.url, enabled: s.enabled }])) as Record<string, { url: string; enabled: boolean }>,
  });
  const save = useMutation({ mutationFn: (body: typeof form) => api<ThreatLists>(URL, { method: "POST", body: JSON.stringify(body) }), onSuccess: onSaved });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(form);
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("threatLists.settingsTitle")}</h2>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit} autoComplete="off">
        <label className="ui-with-brand"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /> <span>{t("threatLists.enabled")}</span></label>
        <label>
          <span>{t("threatLists.prefixes")}</span>
          <textarea rows={4} value={form.operator_prefixes} placeholder="203.0.113.0/24" onChange={(event) => setForm({ ...form, operator_prefixes: event.target.value })} />
        </label>
        <small className="ui-muted">{t("threatLists.prefixesHelp")}</small>
        {data.sources.map((source) => (
          <fieldset key={source.key} className="ui-admin-stack">
            <legend>{source.label}</legend>
            <label className="ui-with-brand">
              <input type="checkbox" checked={form.sources[source.key].enabled}
                onChange={(event) => setForm({ ...form, sources: { ...form.sources, [source.key]: { ...form.sources[source.key], enabled: event.target.checked } } })} />
              <span>{t("threatLists.useSource")}</span>
            </label>
            <label><span>{t("threatLists.url")}</span>
              <input type="url" value={form.sources[source.key].url}
                onChange={(event) => setForm({ ...form, sources: { ...form.sources, [source.key]: { ...form.sources[source.key], url: event.target.value } } })} />
            </label>
          </fieldset>
        ))}
        <button type="submit" className="ui-button" disabled={save.isPending}>{t("cnmaestro.save")}</button>
        {save.isSuccess && <small className="ui-live">{t("threatLists.saved")}</small>}
        {save.error && <small className="ui-backup-error">{(save.error as Error).message}</small>}
      </form>
    </section>
  );
}
