import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { useDeviceFacets } from "../api/devices";
import { type Suggestion, SUGGESTION_KINDS, type SuggestionFilters, useApplySuggestions, useSuggestions } from "../api/operations";
import { BrandIcon } from "../app/BrandIcon";
import { useFormError } from "./CustomerForm";
import { Loading } from "../app/States";

const KIND_TONE: Record<string, string> = {
  plan: "ui-status ui-status-online", legacy: "ui-status ui-status-online", check: "ui-status ui-status-warning",
  plan_open: "ui-status ui-status-pending", soak: "ui-status ui-status-pending", blocked: "ui-status ui-status-offline",
};
const SELECTABLE = new Set(["plan", "check"]);

/** RouterOS devices behind their channel, with the next safe step; nothing upgrades from here. */
export function FirmwareSuggestions() {
  const { t } = useTranslation();
  const search = useSearch({ from: "/firmware/suggestions" });
  const navigate = useNavigate({ from: "/firmware/suggestions" });
  const facets = useDeviceFacets();
  const query = useSuggestions(search);
  const apply = useApplySuggestions();
  const errorText = useFormError();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const data = query.data;
  const update = (patch: Partial<SuggestionFilters>) => {
    setSelected(new Set());
    void navigate({ search: (previous: SuggestionFilters) => ({ ...previous, ...patch }), replace: true });
  };
  const selectable = useMemo(() => (data?.items ?? []).filter((item) => SELECTABLE.has(item.kind)), [data]);
  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };
  const run = (action: "check" | "plan") => {
    if (action === "plan" && !window.confirm(t("firmwareSuggestions.planConfirm"))) return;
    apply.mutate({ action, devices: [...selected] }, { onSuccess: () => setSelected(new Set()) });
  };

  return (
    <section className="ui-page">
      <nav className="ui-breadcrumbs" aria-label={t("device.breadcrumbs")}>
        <Link to="/firmware" search={search.customer ? { customer: search.customer } : {}}>{t("firmware.title")}</Link>
        <span aria-hidden="true">›</span>
        <span>{t("firmwareSuggestions.title")}</span>
      </nav>
      <header className="ui-page-header">
        <h1>{t("firmwareSuggestions.title")}</h1>
        <p>{t("firmwareSuggestions.subtitle", { days: data?.soak_days ?? 7 })} <Link to="/integrations/routeros">{t("firmwareSuggestions.catalog")}</Link></p>
      </header>

      <div className="ui-chips" role="group" aria-label={t("firmwareSuggestions.kindsLabel")}>
        <button type="button" className={`ui-chip${!search.kind ? " is-active" : ""}`} onClick={() => update({ kind: undefined })}>
          {t("firmwareSuggestions.all")} <span className="ui-chip-count">{data?.total ?? "—"}</span>
        </button>
        {SUGGESTION_KINDS.map((kind) => (
          <button key={kind} type="button" className={`ui-chip${kind === "check" || kind === "blocked" ? " ui-chip-warning" : ""}${search.kind === kind ? " is-active" : ""}`} onClick={() => update({ kind })}>
            {t(`firmwareSuggestions.kinds.${kind}`)} <span className="ui-chip-count">{data ? data.counts[kind] ?? 0 : "—"}</span>
          </button>
        ))}
      </div>
      {data && data.security > 0 && <p><span className="ui-severity ui-severity-high">{t("firmwareSuggestions.security")}</span> {t("firmwareSuggestions.securityCount", { count: data.security })}</p>}
      <div className="ui-filter-bar">
        <select value={search.customer ?? ""} onChange={(event) => update({ customer: event.target.value || undefined })} aria-label={t("devices.customer")}>
          <option value="">{t("devices.allCustomers")}</option>
          {facets.data?.customers.map((customer) => <option key={customer.id} value={customer.id}>{customer.name}</option>)}
        </select>
      </div>

      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : data.items.length ? (
        <>
          <div className="ui-data-wrap" aria-busy={query.isFetching}>
            <table className="ui-data-table">
              <thead>
                <tr>
                  <th>
                    <input type="checkbox" aria-label={t("firmwareSuggestions.selectAll")} checked={selectable.length > 0 && selectable.every((item) => selected.has(item.device.id))}
                      onChange={(event) => setSelected(event.target.checked ? new Set(selectable.slice(0, data.max_batch).map((item) => item.device.id)) : new Set())} />
                  </th>
                  {["device", "installed", "target", "reason", "next", "actions"].map((key) => <th key={key}><span className="ui-th">{t(`firmwareSuggestions.columns.${key}`)}</span></th>)}
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => <Row key={item.device.id} item={item} checked={selected.has(item.device.id)} onToggle={() => toggle(item.device.id)} />)}
              </tbody>
            </table>
          </div>
          <div className="ui-row-actions ui-policy-actions">
            <small className="ui-muted">{t("firmwareSuggestions.batchHelp", { count: selected.size, max: data.max_batch })}</small>
            <button type="button" className="ui-button ui-button-ghost" disabled={!selected.size || selected.size > data.max_batch || apply.isPending} onClick={() => run("check")}>
              {t("firmwareSuggestions.check")}
            </button>
            {data.can_execute && (
              <button type="button" className="ui-button ui-button-primary" disabled={!selected.size || selected.size > data.max_batch || apply.isPending} onClick={() => run("plan")}>
                {t("firmwareSuggestions.plan")}
              </button>
            )}
          </div>
        </>
      ) : (
        <div className="ui-empty">{search.customer ? t("firmwareSuggestions.emptyCustomer") : t("firmwareSuggestions.empty")}</div>
      )}

      {apply.isSuccess && (
        <div className={apply.data.skipped.length ? "ui-alert ui-alert-warning" : "ui-alert ui-alert-success"}>
          <p>{t(apply.data.action === "plan" ? "firmwareSuggestions.planned" : "firmwareSuggestions.checked", { count: apply.data.done })}</p>
          {apply.data.skipped.length > 0 && (
            <ul>
              {apply.data.skipped.map((entry) => (
                <li key={entry.id}>{entry.name}: {t(`firmwareSuggestions.skipped.${entry.code}`, { defaultValue: entry.message })}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {apply.error && <div className="ui-alert ui-alert-danger">{errorText(apply.error, "firmwareSuggestions.errors")}</div>}
    </section>
  );
}

function Row({ item, checked, onToggle }: { item: Suggestion; checked: boolean; onToggle: () => void }) {
  const { t } = useTranslation();
  const { device } = item;
  return (
    <tr>
      <td>{SELECTABLE.has(item.kind) && <input type="checkbox" checked={checked} onChange={onToggle} aria-label={t("firmwareSuggestions.select", { name: device.name })} />}</td>
      <td>
        <div className="ui-with-brand">
          <BrandIcon brand={device.brand} small />
          <span>
            <Link to="/devices/$deviceId" params={{ deviceId: device.id }} className="ui-cell-title">{device.name}</Link>
            <small className="ui-muted ui-block">
              {[device.customer?.name, device.site, device.model].filter(Boolean).join(" · ")}
              {item.out_of_support && <> · <span className="ui-text-warning">{t("firmwareSuggestions.outOfSupport")}</span></>}
            </small>
          </span>
        </div>
      </td>
      <td className="ui-mono">{item.installed ?? "—"}</td>
      <td>
        <strong className="ui-mono">{item.target ?? "—"}</strong>
        <small className="ui-muted ui-block">{item.channel ?? "—"}{item.age_days != null ? ` · ${t("firmwareSuggestions.age", { count: item.age_days })}` : ""}</small>
      </td>
      <td>
        {item.security ? (
          <>
            <span className="ui-severity ui-severity-high">{t("firmwareSuggestions.security")}</span>
            {item.reasons.map((reason) => <small key={reason} className="ui-muted ui-block">{reason}</small>)}
          </>
        ) : t("firmwareSuggestions.maintenance")}
      </td>
      <td>
        <span className={KIND_TONE[item.kind]}>{t(`firmwareSuggestions.kinds.${item.kind}`)}</span>
        {item.note && <small className="ui-muted ui-block">{item.note}</small>}
      </td>
      <td>
        <Link to="/devices/$deviceId/firmware-upgrade" params={{ deviceId: device.id }} className="ui-button ui-button-ghost ui-button-small">
          {item.kind === "plan_open" ? t("firmwareSuggestions.openPlan") : item.kind === "legacy" ? t("firmwareSuggestions.legacy") : t("firmwareSuggestions.firmware")}
        </Link>
      </td>
    </tr>
  );
}
