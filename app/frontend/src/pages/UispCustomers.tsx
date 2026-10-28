import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { useDeviceFacets } from "../api/devices";
import { Pager } from "../app/Pager";
import { SearchSelect, customerLabel } from "../app/SearchSelect";
import { Loading } from "../app/States";
import { usePaging } from "../lib/paging";

type SiteState = "matched" | "linked" | "unmatched" | "ignored";

interface SiteRow {
  id: string;
  name: string;
  client: string | null;
  parent: string | null;
  state: SiteState;
  customer: { id: string; name: string; code: string | null } | null;
  devices_in_nsm: number;
}

interface SiteCustomers {
  auto: boolean;
  counts: Record<SiteState, number>;
  can_write: boolean;
  items: SiteRow[];
  imported?: { created: number; linked: number; customers: number };
}

const PATH = "/api/ui/v1/integrations/uisp/site-customers";
const STATES: SiteState[] = ["unmatched", "matched", "linked", "ignored"];

/** UISP endpoints and their NSM customer: matched by name, linked by hand or ignored; their devices are imported automatically. */
export function UispCustomers() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const facets = useDeviceFacets();
  const query = useQuery({ queryKey: ["uisp", "site-customers"], queryFn: () => api<SiteCustomers>(PATH), staleTime: 60_000 });
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<SiteCustomers>(PATH, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: (data) => client.setQueryData(["uisp", "site-customers"], data),
  });
  const [state, setState] = useState<SiteState>("unmatched");
  const [text, setText] = useState("");
  const paging = usePaging(`${state}|${text}`);
  const data = query.data;
  const rows = useMemo(() => {
    const needle = text.trim().toLowerCase();
    return (data?.items ?? []).filter((row) => row.state === state && (!needle || `${row.name} ${row.client ?? ""} ${row.customer?.name ?? ""}`.toLowerCase().includes(needle)));
  }, [data, state, text]);
  const page = rows.slice(paging.offset, paging.offset + paging.size);
  const customers = (facets.data?.customers ?? []).map((c) => ({ value: c.id, label: customerLabel(c) }));

  return (
    <section className="ui-page">
      <nav className="ui-breadcrumbs" aria-label={t("device.breadcrumbs")}>
        <Link to="/integrations">{t("integrations.title")}</Link> / <Link to="/admin/integrations/uisp">UISP</Link> /{" "}
        <Link to="/integrations/uisp/onboarding">{t("onboarding.title")}</Link>
      </nav>
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("uispCustomers.title")}</h1>
          <p>{t("uispCustomers.subtitle")}</p>
        </div>
      </header>
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : (
        <>
          {data.can_write && (
            <label className="ui-with-brand">
              <input type="checkbox" checked={data.auto} disabled={save.isPending} onChange={(event) => save.mutate({ auto: event.target.checked })} />{" "}
              <span>{t("uispCustomers.auto")}</span>
            </label>
          )}
          {save.data?.imported && (save.data.imported.created > 0 || save.data.imported.linked > 0) && (
            <div className="ui-alert ui-alert-success">{t("uispCustomers.imported", save.data.imported)}</div>
          )}
          {save.error && <div className="ui-alert ui-alert-danger">{(save.error as Error).message}</div>}
          <div className="ui-chips" role="group" aria-label={t("uispCustomers.states.label")}>
            {STATES.map((key) => (
              <button key={key} type="button" className={`ui-chip${key === "unmatched" ? " ui-chip-warning" : ""}${state === key ? " is-active" : ""}`}
                onClick={() => setState(key)}>
                {t(`uispCustomers.states.${key}`)} <span className="ui-chip-count">{data.counts[key]}</span>
              </button>
            ))}
          </div>
          <div className="ui-filter-bar">
            <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder={t("uispCustomers.search")} aria-label={t("uispCustomers.search")} />
          </div>
          {page.length === 0 ? (
            <div className="ui-empty">{t("uispCustomers.empty")}</div>
          ) : (
            <div className="ui-data-wrap" aria-busy={query.isFetching || save.isPending}>
              <table className="ui-data-table">
                <thead><tr>{["site", "client", "customer", "devices", ""].map((key) => <th key={key}><span className="ui-th">{key ? t(`uispCustomers.columns.${key}`) : ""}</span></th>)}</tr></thead>
                <tbody>
                  {page.map((row) => (
                    <tr key={row.id}>
                      <td><strong>{row.name}</strong>{row.parent && <small className="ui-muted ui-block">{t("uispCustomers.parent", { name: row.parent })}</small>}</td>
                      <td>{row.client ?? "—"}</td>
                      <td>
                        {row.state === "unmatched" && data.can_write ? (
                          <SearchSelect value="" options={customers} placeholder={t("uispCustomers.choose")} ariaLabel={t("uispCustomers.columns.customer")}
                            onChange={(value) => value && save.mutate({ site_id: row.id, customer_id: value, name: row.client ?? row.name })} />
                        ) : row.customer ? (
                          <Link to="/customers/$customerId" params={{ customerId: row.customer.id }}>{customerLabel(row.customer)}</Link>
                        ) : "—"}
                        {row.state === "matched" && <small className="ui-muted ui-block">{t("uispCustomers.byName")}</small>}
                      </td>
                      <td>{row.devices_in_nsm}</td>
                      <td>
                        {data.can_write && row.state === "unmatched" && (
                          <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={save.isPending}
                            onClick={() => save.mutate({ site_id: row.id, ignore: true })}>{t("uispCustomers.ignore")}</button>
                        )}
                        {data.can_write && (row.state === "linked" || row.state === "ignored") && (
                          <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={save.isPending}
                            onClick={() => save.mutate({ site_id: row.id, reset: true })}>{t("uispCustomers.reset")}</button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Pager total={rows.length} paging={paging} />
            </div>
          )}
        </>
      )}
    </section>
  );
}
