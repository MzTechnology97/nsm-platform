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

interface ZoneHit { zone: string; name: string; codes: string[]; removal: string }
interface Listed {
  address: string; since: string | null; checked_at: string | null; zones: ZoneHit[];
  device: { id: string; name: string; brand: BrandRef } | null; customer: string | null; unassigned: boolean;
}
interface ZoneState { state: string; codes: string[]; message?: string | null; at?: string; ok?: boolean }
interface Catalog { zone: string; name: string; default: boolean; note: string | null }
interface Dnsbl {
  enabled: boolean; zones: string[]; recheck_hours: number; per_run: number; cycle_started_at: string | null; last_cycle_at: string | null;
  zone_errors: Record<string, ZoneState>; health: { at?: string; zones?: Record<string, ZoneState> }; catalog: Catalog[]; prefixes: string[];
  coverage: { addresses: number; position: number }; listed: Listed[]; has_dqs_key: boolean;
}
interface Lookup { address: string; zones: (ZoneHit & { state: string })[] }

const URL = "/api/ui/v1/admin/integrations/dnsbl";
const DQS_ZONE = "zen.spamhaus.org";

/** DNS blacklists (CYBER-01): the operator's public IPv4 addresses checked on the DNSBLs, with the holder and removal links. */
export function AdminDnsbl() {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["admin-dnsbl"], queryFn: () => api<Dnsbl>(URL) });
  const set = (data: Dnsbl) => client.setQueryData(["admin-dnsbl"], data);
  const test = useMutation({ mutationFn: () => api<Dnsbl>(`${URL}/test`, { method: "POST" }), onSuccess: set });
  const run = useMutation({ mutationFn: () => api<Dnsbl & { stats: { addresses?: number; status: string } }>(`${URL}/run`, { method: "POST" }), onSuccess: set });
  const data = query.data;
  const names = Object.fromEntries((data?.catalog ?? []).map((zone) => [zone.zone, zone.name]));
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("dnsbl.title")}</h1>
          <p>{t("dnsbl.subtitle")}</p>
        </div>
        {data && (
          <div className="ui-row-actions">
            <button type="button" className="ui-button ui-button-ghost" disabled={test.isPending} onClick={() => test.mutate()}>{t("dnsbl.test")}</button>
            {data.enabled && <button type="button" className="ui-button" disabled={run.isPending} onClick={() => run.mutate()}>{run.isPending ? t("app.loading") : t("dnsbl.run")}</button>}
          </div>
        )}
      </header>
      <AdminTabs />
      <IntegrationTabs />
      {run.data && <div className="ui-alert ui-alert-success">{run.data.stats.status === "waiting" ? t("dnsbl.waiting") : t("dnsbl.ran", { count: run.data.stats.addresses ?? 0 })}</div>}
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
              <h2>{t("dnsbl.statusTitle")}</h2>
              <p className="ui-muted">{t("dnsbl.coverage", data.coverage)}</p>
              <p className="ui-muted">{data.prefixes.length ? t("dnsbl.prefixes", { list: data.prefixes.join(", ") }) : t("dnsbl.noPrefixes")}{" "}
                <Link to="/admin/integrations/threat-lists">{t("dnsbl.prefixesLink")}</Link></p>
              {data.last_cycle_at && <p className="ui-muted">{t("dnsbl.lastCycle", { when: formatDateTime(data.last_cycle_at, i18n.language) })}</p>}
              {Object.entries(data.zone_errors).map(([zone, error]) => (
                <div key={zone} className="ui-alert ui-alert-warning">
                  {t(error.state === "refused" ? "dnsbl.refused" : "dnsbl.zoneError", { zone: names[zone] ?? zone, detail: error.message || error.codes.join(", ") })}
                  {zone === DQS_ZONE && !data.has_dqs_key && <> {t("dnsbl.dqsHint")}</>}
                </div>
              ))}
              {data.health.zones && (
                <>
                  <h3>{t("dnsbl.healthTitle", { when: formatRelative(data.health.at, i18n.language) })}</h3>
                  <ul className="ui-cap-list">
                    {Object.entries(data.health.zones).map(([zone, state]) => (
                      <li key={zone}>
                        <span className={state.ok ? "ui-live" : "ui-severity ui-severity-warning"}>{state.ok ? t("dnsbl.healthOk") : t(`dnsbl.state.${state.state}`)}</span>
                        {" "}{names[zone] ?? zone} <small className="ui-muted ui-mono">{zone}</small>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </section>
          </div>
          <LookupForm />
          <section className="ui-panel ui-admin-section">
            <h2>{t("dnsbl.listedTitle")}</h2>
            <p className="ui-muted">{t("dnsbl.listedHelp")}</p>
            {data.listed.length === 0 ? (
              <div className="ui-empty">{t("dnsbl.noListed")}</div>
            ) : (
              <div className="ui-data-wrap">
                <table className="ui-data-table">
                  <thead><tr>{["address", "lists", "holder", "since"].map((key) => <th key={key}><span className="ui-th">{t(`dnsbl.columns.${key}`)}</span></th>)}</tr></thead>
                  <tbody>
                    {data.listed.map((row) => (
                      <tr key={row.address}>
                        <td className="ui-mono">{row.address}</td>
                        <td><Hits hits={row.zones} /></td>
                        <td>
                          {row.device ? (
                            <span className="ui-with-brand"><BrandIcon brand={row.device.brand} small />
                              <Link to="/devices/$deviceId" params={{ deviceId: row.device.id }}>{row.device.name}</Link>
                              {row.customer && <small className="ui-muted"> · {row.customer}</small>}</span>
                          ) : row.unassigned ? t("dnsbl.unassigned") : t("dnsbl.unknownHolder")}
                        </td>
                        <td>{row.since ? formatRelative(row.since, i18n.language) : "—"}</td>
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

function Hits({ hits }: { hits: ZoneHit[] }) {
  const { t } = useTranslation();
  return (
    <>
      {hits.map((hit) => (
        <div key={hit.zone}>
          {hit.name} <small className="ui-muted ui-mono">{hit.codes.join(", ")}</small>{" "}
          <a href={hit.removal} target="_blank" rel="noopener noreferrer">{t("dnsbl.removal")}</a>
        </div>
      ))}
    </>
  );
}

function SettingsForm({ data, onSaved }: { data: Dnsbl; onSaved: (data: Dnsbl) => void }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({ enabled: data.enabled, zones: data.zones, recheck_hours: data.recheck_hours, per_run: data.per_run, dqs_key: "" });
  const save = useMutation({ mutationFn: (body: typeof form) => api<Dnsbl>(URL, { method: "POST", body: JSON.stringify(body) }), onSuccess: onSaved });
  const toggle = (zone: string, on: boolean) => setForm({ ...form, zones: on ? [...form.zones, zone] : form.zones.filter((z) => z !== zone) });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(form);
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("dnsbl.settingsTitle")}</h2>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit} autoComplete="off">
        <label className="ui-with-brand"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /> <span>{t("dnsbl.enabled")}</span></label>
        <fieldset className="ui-admin-stack">
          <legend>{t("dnsbl.zones")}</legend>
          {data.catalog.map((zone) => (
            <label key={zone.zone} className="ui-with-brand">
              <input type="checkbox" checked={form.zones.includes(zone.zone)} onChange={(event) => toggle(zone.zone, event.target.checked)} />
              <span>{zone.name} <small className="ui-muted ui-mono">{zone.zone}</small>{zone.note && <small className="ui-muted ui-block">{zone.note}</small>}</span>
            </label>
          ))}
        </fieldset>
        {form.zones.includes(DQS_ZONE) && (
          <label>
            <span>{t("dnsbl.dqsKey")}</span>
            <input type="password" value={form.dqs_key} autoComplete="new-password" placeholder={data.has_dqs_key ? t("dnsbl.dqsKeySaved") : ""}
              onChange={(event) => setForm({ ...form, dqs_key: event.target.value })} />
            <small className="ui-muted">{t("dnsbl.dqsKeyHelp")}</small>
          </label>
        )}
        {(["recheck_hours", "per_run"] as const).map((key) => (
          <label key={key}>
            <span>{t(`dnsbl.fields.${key}`)}</span>
            <input type="number" value={form[key]} onChange={(event) => setForm({ ...form, [key]: Number(event.target.value) })} />
          </label>
        ))}
        <button type="submit" className="ui-button" disabled={save.isPending || form.zones.length === 0}>{t("cnmaestro.save")}</button>
        {save.isSuccess && <small className="ui-live">{t("dnsbl.saved")}</small>}
        {save.error && <small className="ui-backup-error">{(save.error as Error).message}</small>}
      </form>
    </section>
  );
}

function LookupForm() {
  const { t } = useTranslation();
  const [address, setAddress] = useState("");
  const lookup = useMutation({ mutationFn: () => api<Lookup>(`${URL}/lookup`, { method: "POST", body: JSON.stringify({ address }) }) });
  const listed = lookup.data?.zones.filter((zone) => zone.state === "listed") ?? [];
  const failed = lookup.data?.zones.filter((zone) => zone.state === "refused" || zone.state === "error") ?? [];
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("dnsbl.lookupTitle")}</h2>
      <form className="ui-admin-form ui-row-actions" onSubmit={(event) => { event.preventDefault(); lookup.mutate(); }} autoComplete="off">
        <input value={address} onChange={(event) => setAddress(event.target.value)} placeholder="203.0.113.10" inputMode="decimal" aria-label={t("dnsbl.lookupTitle")} />
        <button type="submit" className="ui-button ui-button-ghost" disabled={lookup.isPending || !address.trim()}>{t("dnsbl.lookup")}</button>
      </form>
      {lookup.error && <small className="ui-backup-error">{(lookup.error as Error).message}</small>}
      {lookup.data && (
        <div className="ui-admin-stack">
          <p><strong className="ui-mono">{lookup.data.address}</strong> · {listed.length ? t("dnsbl.lookupListed", { count: listed.length }) : t("dnsbl.lookupClean")}</p>
          {listed.length > 0 && <Hits hits={listed} />}
          {failed.length > 0 && <p className="ui-muted">{t("dnsbl.lookupFailed", { list: failed.map((zone) => zone.name).join(", ") })}</p>}
        </div>
      )}
    </section>
  );
}
