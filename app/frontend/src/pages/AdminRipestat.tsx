import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { AdminTabs } from "../app/AdminTabs";
import { IntegrationTabs } from "../app/IntegrationTabs";
import { Loading } from "../app/States";
import { formatDateTime } from "../lib/format";

interface Declared { prefix: string; origins: number[]; seeing: number | null; total: number | null; problems: string[]; error?: string }
interface Announced { prefix: string; asn: number; rpki: string | null; problems: string[] }
interface Ripestat {
  enabled: boolean; asns: number[]; refresh_hours: number; prefixes: Declared[]; announced: Announced[]; suggested: string[];
  refreshed_at: string | null; errors: string[]; declared: string[]; problem_labels: Record<string, string>; advice: Record<string, string>;
}

const URL = "/api/ui/v1/admin/integrations/ripestat";

/** RIPEstat (RIPESTAT-01): the operator's prefixes as BGP sees them, origin ASNs, visibility and RPKI; read-only. */
export function AdminRipestat() {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["admin-ripestat"], queryFn: () => api<Ripestat>(URL) });
  const set = (data: Ripestat) => client.setQueryData(["admin-ripestat"], data);
  const refresh = useMutation({ mutationFn: () => api<Ripestat>(`${URL}/refresh`, { method: "POST" }), onSuccess: set });
  const data = query.data;
  const problems = (list: string[]) => list.length
    ? list.map((kind) => <span key={kind} className="ui-severity ui-severity-warning" title={data?.advice[kind]}>{data?.problem_labels[kind] ?? kind}</span>)
    : <span className="ui-severity">{t("ripestat.ok")}</span>;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("ripestat.title")}</h1>
          <p>{t("ripestat.subtitle")}</p>
        </div>
        {data?.enabled && (
          <button type="button" className="ui-button" disabled={refresh.isPending} onClick={() => refresh.mutate()}>
            {refresh.isPending ? t("app.loading") : t("ripestat.refresh")}
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
              <h2>{t("ripestat.statusTitle")}</h2>
              <p className="ui-muted">{data.refreshed_at ? t("ripestat.refreshed", { when: formatDateTime(data.refreshed_at, i18n.language) }) : t("ripestat.never")}</p>
              {data.errors.length > 0 && <div className="ui-alert ui-alert-warning">{data.errors.join(" · ")}</div>}
              <p className="ui-muted">{data.declared.length ? t("ripestat.declared", { list: data.declared.join(", ") }) : t("ripestat.noDeclared")}{" "}
                <Link to="/admin/integrations/threat-lists">{t("ripestat.declaredLink")}</Link></p>
              {data.suggested.length > 0 && <div className="ui-alert ui-alert-warning">{t("ripestat.suggested", { list: data.suggested.join(", ") })}</div>}
            </section>
          </div>
          <section className="ui-panel ui-admin-section">
            <h2>{t("ripestat.declaredTitle")}</h2>
            {data.prefixes.length === 0 ? <div className="ui-empty">{t("ripestat.nothing")}</div> : (
              <div className="ui-data-wrap">
                <table className="ui-data-table">
                  <thead><tr>{["prefix", "origins", "visibility", "state"].map((key) => <th key={key}><span className="ui-th">{t(`ripestat.columns.${key}`)}</span></th>)}</tr></thead>
                  <tbody>
                    {data.prefixes.map((row) => (
                      <tr key={row.prefix}>
                        <td className="ui-mono">{row.prefix}</td>
                        <td className="ui-mono">{row.origins.map((asn) => `AS${asn}`).join(", ") || "—"}</td>
                        <td>{row.total ? `${row.seeing ?? 0}/${row.total}` : "—"}</td>
                        <td>{row.error ? <small className="ui-backup-error">{row.error}</small> : problems(row.problems)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          <section className="ui-panel ui-admin-section">
            <h2>{t("ripestat.announcedTitle")}</h2>
            {data.announced.length === 0 ? <div className="ui-empty">{t("ripestat.nothing")}</div> : (
              <div className="ui-data-wrap">
                <table className="ui-data-table">
                  <thead><tr>{["prefix", "asn", "rpki", "state"].map((key) => <th key={key}><span className="ui-th">{t(`ripestat.columns.${key}`)}</span></th>)}</tr></thead>
                  <tbody>
                    {data.announced.map((row) => (
                      <tr key={`${row.asn}-${row.prefix}`}>
                        <td className="ui-mono">{row.prefix}</td>
                        <td className="ui-mono">AS{row.asn}</td>
                        <td>{row.rpki ?? "—"}</td>
                        <td>{problems(row.problems)}</td>
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

function Settings({ data, onSaved }: { data: Ripestat; onSaved: (data: Ripestat) => void }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({ enabled: data.enabled, asns: data.asns.map((asn) => `AS${asn}`).join(" "), refresh_hours: data.refresh_hours });
  const save = useMutation({ mutationFn: (body: typeof form) => api<Ripestat>(URL, { method: "POST", body: JSON.stringify(body) }), onSuccess: onSaved });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(form);
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("ripestat.settingsTitle")}</h2>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit} autoComplete="off">
        <label className="ui-with-brand"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /> <span>{t("ripestat.enabled")}</span></label>
        <label><span>{t("ripestat.asns")}</span>
          <input value={form.asns} placeholder="AS64500" onChange={(event) => setForm({ ...form, asns: event.target.value })} /></label>
        <label><span>{t("ripestat.refreshHours")}</span>
          <input type="number" value={form.refresh_hours} onChange={(event) => setForm({ ...form, refresh_hours: Number(event.target.value) })} /></label>
        <small className="ui-muted">{t("ripestat.help")}</small>
        <button type="submit" className="ui-button" disabled={save.isPending}>{t("cnmaestro.save")}</button>
        {save.isSuccess && <small className="ui-live">{t("ripestat.saved")}</small>}
        {save.error && <small className="ui-backup-error">{(save.error as Error).message}</small>}
      </form>
    </section>
  );
}
