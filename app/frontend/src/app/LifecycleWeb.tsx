import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../api/client";
import { formatDate } from "../lib/format";

interface WebCandidate {
  product_id: string; name: string; vendor: string; status: string; end_of_sale: string | null; end_of_updates: string | null;
  replacement: string | null; source_title: string | null; source_url: string | null; page_url: string | null; read_at: string | null;
  notes: string | null; confidence: string;
}
interface WebLookup {
  id: string; vendor: string; model: string; query: string | null; status: "found" | "not_found" | "error" | "new"; error: string | null;
  checked_at: string | null; devices: number; candidates: WebCandidate[]; applied_product: string | null; applied_at: string | null;
  record: { id: string; model: string; source: string } | null;
}
interface WebState { enabled: boolean; provider: string; provider_url: string; keyed: boolean; items: WebLookup[]; pending: number; can_manage: boolean }
type RunResult = { checked: number; found: number; not_found: number; errors: number; deferred: number };

function message(error: unknown) {
  return error instanceof ApiError ? error.message : (error as Error | null)?.message;
}

/** Lifecycle catalog: EOL/EOS notices found on the web for the inventory models, applied by an operator. */
export function LifecycleWebPanel() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["lifecycle-web"], queryFn: () => api<WebState>("/api/ui/v1/lifecycle/web") });
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["lifecycle-web"] });
    void client.invalidateQueries({ queryKey: ["lifecycle-catalog"] });
    void client.invalidateQueries({ queryKey: ["lifecycle"] });
  };
  const run = useMutation({ mutationFn: () => api<RunResult>("/api/ui/v1/lifecycle/web/run", { method: "POST", body: "{}" }), onSuccess: refresh });
  const exact = useMutation({ mutationFn: () => api<{ applied: number; skipped: number }>("/api/ui/v1/lifecycle/web/apply-exact", { method: "POST", body: "{}" }), onSuccess: refresh });
  const data = query.data;
  if (!data) return null;
  const ready = data.items.filter((item) => item.candidates.some((c) => c.confidence === "exact" && c.product_id !== item.applied_product)).length;
  return (
    <section className="ui-panel ui-admin-section ui-section-gap" id="lifecycle-web">
      <div className="ui-panel-header">
        <div>
          <h2>{t("lifecycleWeb.title")}</h2>
          <p className="ui-muted">{t("lifecycleWeb.hint", { provider: data.provider })} <a href={data.provider_url} target="_blank" rel="noopener noreferrer">{data.provider}</a></p>
        </div>
      </div>
      {!data.enabled && <div className="ui-alert">{t("lifecycleWeb.disabled")}</div>}
      <p className="ui-muted">{t(data.keyed ? "lifecycleWeb.keyed" : "lifecycleWeb.keyless")} {data.pending > 0 && t("lifecycleWeb.pending", { count: data.pending })}</p>
      {run.isSuccess && <div className="ui-alert ui-alert-success">{t("lifecycleWeb.ran", run.data)}{run.data.deferred > 0 && ` ${t("lifecycleWeb.deferred", { count: run.data.deferred })}`}</div>}
      {exact.isSuccess && <div className="ui-alert ui-alert-success">{t("lifecycleWeb.appliedAll", exact.data)}</div>}
      {(run.error || exact.error) && <div className="ui-alert ui-alert-danger">{message(run.error ?? exact.error)}</div>}
      {data.can_manage && data.enabled && (
        <div className="ui-row-actions">
          <button type="button" className="ui-button ui-button-primary" disabled={run.isPending} onClick={() => run.mutate()}>{t("lifecycleWeb.run")}</button>
          <button type="button" className="ui-button" disabled={exact.isPending || !ready} onClick={() => exact.mutate()}>{t("lifecycleWeb.applyExact", { count: ready })}</button>
        </div>
      )}
      {data.items.length > 0 ? (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead>
              <tr>{["model", "found", "dates", "source", "action"].map((key) => <th key={key}><span className="ui-th">{t(`lifecycleWeb.columns.${key}`)}</span></th>)}</tr>
            </thead>
            <tbody>{data.items.map((item) => <WebRow key={item.id} item={item} canManage={data.can_manage && data.enabled} onDone={refresh} />)}</tbody>
          </table>
        </div>
      ) : (
        <div className="ui-empty">{t("lifecycleWeb.empty")}</div>
      )}
    </section>
  );
}

function WebRow({ item, canManage, onDone }: { item: WebLookup; canManage: boolean; onDone: () => void }) {
  const { t, i18n } = useTranslation();
  const [name, setName] = useState(item.query ?? "");
  const [editing, setEditing] = useState(false);
  const search = useMutation({
    mutationFn: (q: string) => api(`/api/ui/v1/lifecycle/web/${item.id}/search`, { method: "POST", body: JSON.stringify({ query: q }) }),
    onSuccess: () => { setEditing(false); onDone(); },
  });
  const apply = useMutation({
    mutationFn: (product: string) => api<{ linked: number }>(`/api/ui/v1/lifecycle/web/${item.id}/apply`, { method: "POST", body: JSON.stringify({ product_id: product }) }),
    onSuccess: onDone,
  });
  const date = (value: string | null) => (value ? formatDate(value, i18n.language) : "—");
  const best = item.candidates[0];
  return (
    <tr>
      <td>
        <span className="ui-muted">{item.vendor}</span> <strong>{item.model}</strong>
        <small className="ui-muted ui-block">{t("lifecycleWeb.devices", { count: item.devices })}{item.query && ` · ${t("lifecycleWeb.searchedAs", { name: item.query })}`}</small>
      </td>
      <td>
        {item.status === "found" && best ? (
          <>
            <strong>{best.name}</strong> <span className="ui-severity">{t(`lifecycleWeb.vendorStatus.${best.status}`, { defaultValue: best.status })}</span>
            <small className="ui-muted ui-block">{t(`lifecycleWeb.confidence.${best.confidence}`, { defaultValue: best.confidence })}
              {item.candidates.length > 1 && ` · ${t("lifecycleWeb.others", { count: item.candidates.length - 1 })}`}</small>
            {best.replacement && <small className="ui-muted ui-block">{t("lifecycleWeb.replacement", { name: best.replacement })}</small>}
          </>
        ) : item.status === "not_found" ? (
          <span className="ui-muted">{t("lifecycleWeb.notFound")}</span>
        ) : item.status === "error" ? (
          <span className="ui-backup-error">{t("lifecycleWeb.error", { error: item.error ?? "" })}</span>
        ) : (
          <span className="ui-muted">{t("lifecycleWeb.notChecked")}</span>
        )}
        {item.checked_at && <small className="ui-muted ui-block">{t("lifecycleWeb.checked", { when: date(item.checked_at) })}</small>}
      </td>
      <td className="ui-nowrap">
        {best && (
          <>
            {t("lifecycleWeb.endOfSale")}: {best.end_of_sale ? date(best.end_of_sale) : t("lifecycleWeb.unpublished")}
            <small className="ui-block">{t("lifecycleWeb.endOfUpdates")}: {best.end_of_updates ? date(best.end_of_updates) : t("lifecycleWeb.unpublished")}</small>
          </>
        )}
      </td>
      <td>
        {best?.source_url && <a href={best.source_url} target="_blank" rel="noopener noreferrer">{best.source_title ?? best.vendor}</a>}
        {best?.page_url && <small className="ui-block"><a href={best.page_url} target="_blank" rel="noopener noreferrer">eol.network</a></small>}
        {best?.read_at && <small className="ui-muted ui-block">{t("lifecycleWeb.readAt", { when: date(best.read_at) })}</small>}
      </td>
      <td>
        {item.applied_product && <small className="ui-block">{t("lifecycleWeb.applied", { when: date(item.applied_at) })}</small>}
        {!item.applied_product && item.record && <small className="ui-muted ui-block">{t("lifecycleWeb.inCatalog", { source: item.record.source })}</small>}
        {canManage && (
          <div className="ui-row-actions">
            {item.candidates.map((c) => c.product_id !== item.applied_product && (
              <button key={c.product_id} type="button" className="ui-button ui-button-small" disabled={apply.isPending} onClick={() => apply.mutate(c.product_id)}>
                {item.candidates.length > 1 ? t("lifecycleWeb.applyThis", { name: c.product_id }) : t("lifecycleWeb.apply")}
              </button>
            ))}
            {editing ? (
              <form onSubmit={(event) => { event.preventDefault(); search.mutate(name.trim()); }} className="ui-row-actions">
                <input value={name} onChange={(event) => setName(event.target.value)} maxLength={150} aria-label={t("lifecycleWeb.otherName")} placeholder={t("lifecycleWeb.otherNamePlaceholder")} />
                <button type="submit" className="ui-button ui-button-small" disabled={search.isPending}>{t("lifecycleWeb.search")}</button>
              </form>
            ) : (
              <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={() => setEditing(true)}>{t("lifecycleWeb.otherName")}</button>
            )}
          </div>
        )}
        {(apply.error || search.error) && <small className="ui-backup-error ui-block">{message(apply.error ?? search.error)}</small>}
      </td>
    </tr>
  );
}
