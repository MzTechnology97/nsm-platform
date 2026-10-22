import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { type IspbillingFilters, type IspbillingRow, useIspbillingCommit, useIspbillingCustomers, useIspbillingPreview } from "../api/ispbilling";
import { formatDateTime } from "../lib/format";
import { useDebounced } from "../lib/hooks";
import { Loading } from "../app/States";

const STATES = ["new", "linkable", "linked"] as const;
const MAX_BATCH = 300;

/** Customers and sites from ISPBilling: pick, preview, import (create or link; sites added, nothing renamed or removed). */
export function IspbillingImport() {
  const { t, i18n } = useTranslation();
  const search = useSearch({ strict: false }) as IspbillingFilters;
  const navigate = useNavigate();
  const [text, setText] = useState(search.q ?? "");
  const q = useDebounced(text.trim());
  const state = search.state ?? "new";
  const query = useIspbillingCustomers({ ...search, state });
  const first = query.data?.pages[0];
  const items = useMemo(() => query.data?.pages.flatMap((page) => page.items) ?? [], [query.data]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const preview = useIspbillingPreview();
  const commit = useIspbillingCommit();
  const update = (patch: Partial<IspbillingFilters>) => {
    setSelected(new Set());
    preview.reset();
    void navigate({ to: "/integrations/ispbilling/import", search: { ...search, ...patch } as never, replace: true });
  };

  useEffect(() => {
    if ((search.q ?? "") !== q) update({ q: q || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const toggle = (id: string) => setSelected((previous) => {
    const next = new Set(previous);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });
  const visibleIds = items.map((row) => row.external_id);
  const allSelected = visibleIds.length > 0 && visibleIds.every((id) => selected.has(id));
  const actionable = preview.data?.plan.filter((row) => row.action !== "skip") ?? [];

  if (commit.data) {
    return (
      <section className="ui-page">
        <header className="ui-page-header"><h1>{t("ispbilling.doneTitle")}</h1></header>
        <div className="ui-alert">
          {t("ispbilling.done", { created: commit.data.created, linked: commit.data.linked, sites: commit.data.sites_created, sitesLinked: commit.data.sites_linked })}
          {commit.data.skipped.length > 0 && ` ${t("ispbilling.skipped", { count: commit.data.skipped.length })}`}
        </div>
        <div className="ui-row-actions">
          <Link className="ui-button" to="/customers">{t("ispbilling.openCustomers")}</Link>
          <button type="button" className="ui-button ui-button-ghost" onClick={() => { commit.reset(); preview.reset(); setSelected(new Set()); }}>{t("onboarding.again")}</button>
        </div>
      </section>
    );
  }

  return (
    <section className="ui-page">
      <nav className="ui-breadcrumbs" aria-label={t("device.breadcrumbs")}>
        <Link to="/integrations">{t("integrations.title")}</Link> / <Link to="/admin/integrations/ispbilling">ISPBilling</Link>
      </nav>
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("ispbilling.importTitle")}</h1>
          <p>{t("ispbilling.importSubtitle")}</p>
        </div>
      </header>
      {first && !first.configured && <div className="ui-alert ui-alert-warning">{t("ispbilling.notConfigured")}</div>}
      {first?.configured && (
        <p className="ui-muted">
          {first.last_sync_at ? t("ispbilling.mirrorAt", { when: formatDateTime(first.last_sync_at, i18n.language) }) : t("ispbilling.mirrorNever")}
          {first.details_pending ? ` ${t("ispbilling.addressesPending", { count: first.details_pending })}` : ""}
        </p>
      )}
      <div className="ui-chips" role="group" aria-label={t("onboarding.stateFilter")}>
        {STATES.map((key) => (
          <button key={key} type="button" className={`ui-chip${key === "linkable" ? " ui-chip-warning" : ""}${state === key ? " is-active" : ""}`} onClick={() => update({ state: key })}>
            {t(`ispbilling.states.${key}`)} <span className="ui-chip-count">{first ? first.counts[key] : "—"}</span>
          </button>
        ))}
      </div>
      <div className="ui-filter-bar">
        <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder={t("ispbilling.searchPlaceholder")} aria-label={t("ispbilling.search")} />
        <select value={search.status ?? ""} onChange={(event) => update({ status: event.target.value || undefined })} aria-label={t("ispbilling.status")}>
          <option value="">{t("ispbilling.allStatuses")}</option>
          {first?.statuses.map((value) => <option key={value} value={value}>{t(`ispbilling.statuses.${value}`, { defaultValue: value })}</option>)}
        </select>
        <select value={search.group ?? ""} onChange={(event) => update({ group: event.target.value || undefined })} aria-label={t("ispbilling.group")}>
          <option value="">{t("ispbilling.allGroups")}</option>
          {first?.groups.map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
      </div>

      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !first ? (
        <Loading />
      ) : items.length ? (
        <>
          <div className="ui-data-wrap" aria-busy={query.isFetching}>
            <table className="ui-data-table">
              <thead>
                <tr>
                  <th><input type="checkbox" checked={allSelected} aria-label={t("onboarding.selectAll")} onChange={() => setSelected(allSelected ? new Set() : new Set(visibleIds.slice(0, MAX_BATCH)))} /></th>
                  {["customer", "kind", "status", "sites", "nsm"].map((key) => <th key={key}><span className="ui-th">{t(`ispbilling.columns.${key}`)}</span></th>)}
                </tr>
              </thead>
              <tbody>
                {items.map((row) => <Row key={row.external_id} row={row} checked={selected.has(row.external_id)} onToggle={toggle} />)}
              </tbody>
            </table>
          </div>
          <div className="ui-table-foot">
            <span>{t("firmware.shown", { shown: items.length, total: first.total })}</span>
            {query.hasNextPage && (
              <button type="button" className="ui-button ui-button-ghost" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
                {query.isFetchingNextPage ? t("app.loading") : t("firmware.more")}
              </button>
            )}
          </div>
          <div className="ui-row-actions">
            <button type="button" className="ui-button" disabled={!selected.size || selected.size > MAX_BATCH || preview.isPending} onClick={() => preview.mutate([...selected])}>
              {t("ispbilling.preview", { count: selected.size, max: MAX_BATCH })}
            </button>
            {preview.error && <small className="ui-backup-error">{(preview.error as Error).message}</small>}
          </div>
        </>
      ) : (
        <div className="ui-empty">{search.q || search.status || search.group ? t("onboarding.emptyFiltered") : t("ispbilling.empty")}</div>
      )}

      {preview.data && (
        <section className="ui-panel ui-admin-section" aria-live="polite">
          <h2>{t("onboarding.previewTitle")}</h2>
          <p className="ui-muted">{t("ispbilling.previewHelp")}</p>
          <div className="ui-data-wrap">
            <table className="ui-data-table">
              <thead><tr>{["customer", "action", "sites"].map((key) => <th key={key}><span className="ui-th">{t(`ispbilling.columns.${key}`)}</span></th>)}</tr></thead>
              <tbody>
                {preview.data.plan.map((row) => (
                  <tr key={row.external_id}>
                    <td><strong>{row.name ?? row.external_id}</strong>{row.code && <small className="ui-muted ui-block">{row.code}</small>}</td>
                    <td>
                      <span className="ui-status">{t(`ispbilling.actions.${row.action}`)}</span>
                      {row.customer && <small className="ui-muted ui-block">{t(row.match === "name" ? "ispbilling.matchName" : "ispbilling.matchCode", { name: row.customer.name })}</small>}
                      {row.code_in_use && <small className="ui-key-hint ui-block">{t("ispbilling.codeInUse")}</small>}
                      {row.reason && <small className="ui-key-hint ui-block">{row.reason}</small>}
                    </td>
                    <td>
                      {row.sites_new?.map((site) => <small key={`n-${site.address}`} className="ui-block">+ {site.name}</small>)}
                      {row.sites_existing?.map((site) => <small key={`e-${site.address}`} className="ui-muted ui-block">{t("ispbilling.siteExisting", { name: site.name })}</small>)}
                      {!row.sites_new?.length && !row.sites_existing?.length && <small className="ui-muted">—</small>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="ui-row-actions">
            <button type="button" className="ui-button ui-button-ghost" onClick={() => preview.reset()}>{t("incidentDetail.cancel")}</button>
            {actionable.length > 0 && (
              <button type="button" className="ui-button" disabled={commit.isPending} onClick={() => commit.mutate(actionable.map((row) => row.external_id))}>
                {t("ispbilling.confirm", { count: actionable.length })}
              </button>
            )}
            {commit.error && <small className="ui-backup-error">{(commit.error as Error).message}</small>}
          </div>
        </section>
      )}
    </section>
  );
}

function Row({ row, checked, onToggle }: { row: IspbillingRow; checked: boolean; onToggle: (id: string) => void }) {
  const { t } = useTranslation();
  return (
    <tr>
      <td><input type="checkbox" checked={checked} onChange={() => onToggle(row.external_id)} aria-label={t("onboarding.select", { name: row.name })} /></td>
      <td><strong>{row.name}</strong><small className="ui-muted ui-block">{[row.code, row.group].filter(Boolean).join(" · ") || "—"}</small></td>
      <td>{row.kind ? t(`ispbilling.kinds.${row.kind}`, { defaultValue: row.kind }) : "—"}</td>
      <td>{row.status ? t(`ispbilling.statuses.${row.status}`, { defaultValue: row.status }) : "—"}</td>
      <td>
        {row.sites.length ? row.sites.map((site) => (
          <small key={site.address} className={`ui-block${site.site ? " ui-muted" : ""}`} title={site.address}>{site.site ? `✓ ${site.name}` : site.name}</small>
        )) : <small className="ui-muted">{t("ispbilling.noSites")}</small>}
      </td>
      <td>
        <span className="ui-status">{t(`ispbilling.states.${row.state}`)}</span>
        {row.customer && (
          <small className="ui-block">
            <Link to="/customers/$customerId" params={{ customerId: row.customer.id }}>{row.customer.name}</Link>
            {row.state === "linkable" && ` · ${t(row.match === "name" ? "ispbilling.byName" : "ispbilling.byCode")}`}
          </small>
        )}
      </td>
    </tr>
  );
}
