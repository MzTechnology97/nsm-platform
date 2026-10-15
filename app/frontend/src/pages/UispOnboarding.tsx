import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { type OnboardingFilters, type OnboardingRow, useOnboarding, useOnboardingCommit, useOnboardingPreview } from "../api/admin";
import { BrandIcon } from "../app/BrandIcon";
import { useDebounced } from "../lib/hooks";

const STATES = ["new", "linkable", "linked", "blocked"] as const;
const SELECTABLE = new Set(["new", "linkable"]);

export function UispOnboarding() {
  const { t } = useTranslation();
  const search = useSearch({ from: "/integrations/uisp/onboarding" });
  const navigate = useNavigate({ from: "/integrations/uisp/onboarding" });
  const [text, setText] = useState(search.q ?? "");
  const q = useDebounced(text.trim());
  const state = search.state ?? "new";
  const query = useOnboarding({ ...search, state });
  const first = query.data?.pages[0];
  const items = useMemo(() => query.data?.pages.flatMap((page) => page.items) ?? [], [query.data]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [customer, setCustomer] = useState("");
  const [site, setSite] = useState("");
  const preview = useOnboardingPreview();
  const commit = useOnboardingCommit();
  const update = (patch: Partial<OnboardingFilters>) => {
    setSelected(new Set());
    preview.reset();
    void navigate({ search: (previous: OnboardingFilters) => ({ ...previous, ...patch }), replace: true });
  };

  useEffect(() => {
    if ((search.q ?? "") !== q) update({ q: q || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const selectable = SELECTABLE.has(state);
  const toggle = (id: string) => setSelected((previous) => {
    const next = new Set(previous);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });
  const visibleIds = items.filter((row) => row.external_id && SELECTABLE.has(row.state)).map((row) => row.external_id as string);
  const allSelected = visibleIds.length > 0 && visibleIds.every((id) => selected.has(id));
  const selection = { external_ids: [...selected], customer_id: customer, site_id: site };
  const sites = first?.sites.filter((item) => item.customer_id === customer) ?? [];

  if (commit.data) {
    return (
      <section className="ui-page">
        <header className="ui-page-header"><h1>{t("onboarding.doneTitle")}</h1></header>
        <div className="ui-alert">
          {t("onboarding.done", { created: commit.data.created, linked: commit.data.linked })}
          {commit.data.skipped.length > 0 && ` ${t("onboarding.skipped", { count: commit.data.skipped.length, names: commit.data.skipped.slice(0, 5).join(", ") })}`}
        </div>
        <div className="ui-row-actions">
          <Link className="ui-button" to="/customers/$customerId" params={{ customerId: commit.data.customer_id }}>{t("onboarding.openCustomer")}</Link>
          <button type="button" className="ui-button ui-button-ghost" onClick={() => { commit.reset(); preview.reset(); setSelected(new Set()); }}>{t("onboarding.again")}</button>
        </div>
      </section>
    );
  }

  return (
    <section className="ui-page">
      <nav className="ui-breadcrumbs" aria-label={t("device.breadcrumbs")}>
        <Link to="/integrations">{t("integrations.title")}</Link> / <Link to="/admin/integrations/uisp">UISP</Link>
      </nav>
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("onboarding.title")}</h1>
          <p>{t("onboarding.subtitle")}</p>
        </div>
      </header>
      {first?.error && <div className="ui-alert ui-alert-danger">{first.error}</div>}
      <div className="ui-chips" role="group" aria-label={t("onboarding.stateFilter")}>
        {STATES.map((key) => (
          <button key={key} type="button" className={`ui-chip${key === "linkable" ? " ui-chip-warning" : ""}${state === key ? " is-active" : ""}`} onClick={() => update({ state: key })}>
            {t(`onboarding.states.${key}`)} <span className="ui-chip-count">{first ? first.counts[key] : "—"}</span>
          </button>
        ))}
      </div>
      <div className="ui-filter-bar">
        <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder={t("onboarding.searchPlaceholder")} aria-label={t("onboarding.search")} />
        <select value={search.uisp_site ?? ""} onChange={(event) => update({ uisp_site: event.target.value || undefined })} aria-label={t("onboarding.uispSite")}>
          <option value="">{t("onboarding.allSites")}</option>
          {first?.uisp_sites.map((name) => <option key={name} value={name}>{name}</option>)}
        </select>
      </div>

      {selectable && (
        <div className="ui-admin-form">
          <label>
            <span>{t("onboarding.customer")}</span>
            <select value={customer} onChange={(event) => { setCustomer(event.target.value); setSite(""); preview.reset(); }} required>
              <option value="">{t("onboarding.choose")}</option>
              {first?.customers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label>
            <span>{t("onboarding.site")}</span>
            <select value={site} onChange={(event) => { setSite(event.target.value); preview.reset(); }} disabled={!customer}>
              <option value="">{t("onboarding.noSite")}</option>
              {sites.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <small className="ui-muted ui-admin-wide">{t("onboarding.customerHelp")}</small>
        </div>
      )}

      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !first ? (
        <div className="ui-empty">{t("app.loading")}</div>
      ) : items.length ? (
        <>
          <div className="ui-data-wrap" aria-busy={query.isFetching}>
            <table className="ui-data-table">
              <thead>
                <tr>
                  <th>
                    {selectable && (
                      <input type="checkbox" checked={allSelected} aria-label={t("onboarding.selectAll")}
                        onChange={() => setSelected(allSelected ? new Set() : new Set(visibleIds.slice(0, first.max_batch)))} />
                    )}
                  </th>
                  {["device", "model", "address", "uispSite", "type", "nsm"].map((key) => <th key={key}><span className="ui-th">{t(`onboarding.columns.${key}`)}</span></th>)}
                </tr>
              </thead>
              <tbody>
                {items.map((row) => <Row key={row.external_id ?? row.primary_mac ?? row.device_identity ?? ""} row={row} selectable={selectable} checked={!!row.external_id && selected.has(row.external_id)} onToggle={toggle} />)}
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
          {selectable && (
            <div className="ui-row-actions">
              <button type="button" className="ui-button" disabled={!selected.size || !customer || selected.size > first.max_batch || preview.isPending}
                onClick={() => preview.mutate(selection)}>
                {t("onboarding.preview", { count: selected.size, max: first.max_batch })}
              </button>
              {preview.error && <small className="ui-backup-error">{(preview.error as Error).message}</small>}
            </div>
          )}
        </>
      ) : (
        <div className="ui-empty">{search.q || search.uisp_site ? t("onboarding.emptyFiltered") : t("onboarding.empty")}</div>
      )}

      {preview.data && (
        <section className="ui-panel ui-admin-section" aria-live="polite">
          <h2>{t("onboarding.previewTitle")}</h2>
          <p className="ui-muted">{t(preview.data.site ? "onboarding.previewHelpSite" : "onboarding.previewHelp", { customer: preview.data.customer.name, site: preview.data.site?.name })}</p>
          <div className="ui-data-wrap">
            <table className="ui-data-table">
              <thead><tr>{["device", "model", "address", "type", "action"].map((key) => <th key={key}><span className="ui-th">{t(`onboarding.columns.${key}`)}</span></th>)}</tr></thead>
              <tbody>
                {preview.data.plan.map((row) => (
                  <tr key={row.external_id ?? ""}>
                    <td><div className="ui-with-brand"><BrandIcon brand={row.brand} small /><strong>{row.device_identity ?? row.external_id}</strong></div></td>
                    <td>{row.model ?? "—"}</td>
                    <td><code>{row.primary_mac ?? "—"}</code></td>
                    <td>{row.device_type ?? "—"}</td>
                    <td>
                      <span className="ui-status">{t(`onboarding.actions.${row.action}`)}</span>
                      {row.action === "link" && row.device && <small className="ui-muted ui-block">{t("onboarding.staysInCustomer", { name: row.device.name })}</small>}
                      {row.reason && <small className="ui-key-hint ui-block">{row.reason}</small>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="ui-row-actions">
            <button type="button" className="ui-button ui-button-ghost" onClick={() => preview.reset()}>{t("incidentDetail.cancel")}</button>
            {preview.data.plan.some((row) => row.action === "create" || row.action === "link") && (
              <button type="button" className="ui-button" disabled={commit.isPending}
                onClick={() => commit.mutate({ ...selection, external_ids: preview.data.plan.filter((row) => row.action === "create" || row.action === "link").map((row) => row.external_id as string) })}>
                {t("onboarding.confirm", { count: preview.data.plan.filter((row) => row.action === "create" || row.action === "link").length })}
              </button>
            )}
            {commit.error && <small className="ui-backup-error">{(commit.error as Error).message}</small>}
          </div>
        </section>
      )}
    </section>
  );
}

function Row({ row, selectable, checked, onToggle }: { row: OnboardingRow; selectable: boolean; checked: boolean; onToggle: (id: string) => void }) {
  const { t } = useTranslation();
  const canPick = selectable && !!row.external_id && SELECTABLE.has(row.state);
  return (
    <tr>
      <td>{canPick && <input type="checkbox" checked={checked} onChange={() => onToggle(row.external_id as string)} aria-label={t("onboarding.select", { name: row.device_identity ?? row.external_id })} />}</td>
      <td>
        <div className="ui-with-brand">
          <BrandIcon brand={row.brand} small />
          <span><strong>{row.device_identity ?? "—"}</strong>{row.firmware_version && <small className="ui-muted ui-block">{row.firmware_version}</small>}</span>
        </div>
      </td>
      <td>{row.model ?? "—"}</td>
      <td><code>{row.primary_mac ?? "—"}</code>{row.management_ip && <small className="ui-muted ui-block">{row.management_ip}</small>}</td>
      <td>{row.site ?? "—"}</td>
      <td>{row.device_type ?? "—"}</td>
      <td>
        <span className="ui-status">{t(`onboarding.states.${row.state}`)}</span>
        {row.device && <small className="ui-block"><Link to="/devices/$deviceId" params={{ deviceId: row.device.id }}>{row.device.name}</Link></small>}
        {row.reason && <small className="ui-key-hint ui-block">{row.reason}</small>}
      </td>
    </tr>
  );
}
