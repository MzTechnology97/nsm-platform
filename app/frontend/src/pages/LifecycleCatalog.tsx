import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { type FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { type CatalogFilters, type CatalogRecord, useCatalogForm, useCatalogWrite, useLifecycleCatalog } from "../api/security";
import { formatDate, formatDateTime } from "../lib/format";
import { useDebounced } from "../lib/hooks";
import { useFormError } from "./CustomerForm";

const STATUS_TONE = { supported: "ui-severity", eol: "ui-severity ui-severity-warning", eos: "ui-severity ui-severity-high" } as const;

/** EOL/EOS catalog: one record per model (with aliases) and its source; devices are correlated by vendor + model. */
export function LifecycleCatalog() {
  const { t, i18n } = useTranslation();
  const search = useSearch({ from: "/lifecycle/catalog" });
  const navigate = useNavigate({ from: "/lifecycle/catalog" });
  const [text, setText] = useState(search.q ?? "");
  const q = useDebounced(text.trim());
  useEffect(() => {
    if ((search.q ?? "") !== q) void navigate({ search: (previous: CatalogFilters) => ({ ...previous, q: q || undefined }), replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);
  const query = useLifecycleCatalog({ q: search.q, vendor: search.vendor });
  const data = query.data;

  return (
    <section className="ui-page">
      <nav className="ui-breadcrumbs" aria-label={t("device.breadcrumbs")}>
        <Link to="/lifecycle">{t("lifecycle.title")}</Link>
        <span aria-hidden="true">›</span>
        <span>{t("lifecycleCatalog.title")}</span>
      </nav>
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("lifecycleCatalog.title")}</h1>
          <p>{t("lifecycleCatalog.subtitle", { count: data?.total ?? 0 })}</p>
        </div>
        <div className="ui-actions">
          {data && <a className="ui-button ui-button-ghost" href={data.csv_url}>{t("lifecycleCatalog.export")}</a>}
          {data?.can_manage && <Link to="/lifecycle/catalog/new" className="ui-button ui-button-primary">{t("lifecycleCatalog.new")}</Link>}
        </div>
      </header>

      <div className="ui-filter-bar">
        <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder={t("lifecycleCatalog.search")} aria-label={t("lifecycleCatalog.search")} autoComplete="off" />
        <select value={search.vendor ?? ""} onChange={(event) => void navigate({ search: (previous: CatalogFilters) => ({ ...previous, vendor: event.target.value || undefined }), replace: true })}
          aria-label={t("lifecycleCatalog.vendor")}>
          <option value="">{t("lifecycleCatalog.allVendors")}</option>
          {data?.vendors.map((vendor) => <option key={vendor} value={vendor}>{vendor}</option>)}
        </select>
      </div>

      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <div className="ui-empty">{t("app.loading")}</div>
      ) : data.items.length ? (
        <div className="ui-data-wrap" aria-busy={query.isFetching}>
          <table className="ui-data-table">
            <thead>
              <tr>{["model", "state", "eol", "eos", "source", "devices"].map((key) => <th key={key}><span className="ui-th">{t(`lifecycleCatalog.columns.${key}`)}</span></th>)}</tr>
            </thead>
            <tbody>
              {data.items.map((row) => (
                <tr key={row.id}>
                  <td>
                    <span className="ui-muted">{row.vendor}</span>{" "}
                    {data.can_manage ? <Link to="/lifecycle/catalog/$recordId/edit" params={{ recordId: row.id }} className="ui-cell-title">{row.model}</Link> : <strong>{row.model}</strong>}
                    {row.aliases.length > 0 && <small className="ui-muted ui-block">{t("lifecycleCatalog.aliases")}: {row.aliases.join(", ")}</small>}
                  </td>
                  <td><span className={STATUS_TONE[row.status]}>{t(`lifecycle.status.${row.status}`, { defaultValue: row.status })}</span></td>
                  <td className="ui-nowrap">{row.eol_date ? formatDate(row.eol_date, i18n.language) : "—"}</td>
                  <td className="ui-nowrap">{row.eos_date ? formatDate(row.eos_date, i18n.language) : "—"}</td>
                  <td>
                    {row.source_url ? <a href={row.source_url} target="_blank" rel="noopener noreferrer">{row.source}</a> : row.source}
                    <small className="ui-muted ui-block">{t("lifecycleCatalog.verified", { when: formatDate(row.evidence_date, i18n.language) })}</small>
                  </td>
                  <td>{row.devices ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ui-empty">{search.q || search.vendor ? t("lifecycleCatalog.noMatch") : t("lifecycleCatalog.empty")}</div>
      )}

      {data?.can_manage && <ImportPanel fields={data.csv_fields} />}
    </section>
  );
}

function ImportPanel({ fields }: { fields: string[] }) {
  const { t } = useTranslation();
  const errorText = useFormError();
  const [file, setFile] = useState<File | null>(null);
  const upload = useCatalogWrite<File, { created: number; updated: number; correlated: number; status_changed: number }>("/import", (input) => {
    const form = new FormData();
    form.append("file", input);
    return form;
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (file) upload.mutate(file);
  };
  return (
    <section className="ui-panel ui-admin-section ui-section-gap">
      <h2>{t("lifecycleCatalog.importTitle")}</h2>
      <p className="ui-muted">{t("lifecycleCatalog.importHelp")}</p>
      <p className="ui-muted"><code>{fields.join(",")}</code></p>
      <form className="ui-admin-form" onSubmit={submit}>
        <label><span>{t("lifecycleCatalog.file")}</span><input type="file" accept=".csv,text/csv" onChange={(event) => setFile(event.target.files?.[0] ?? null)} required /></label>
        <button type="submit" className="ui-button" disabled={!file || upload.isPending}>{t("lifecycleCatalog.import")}</button>
      </form>
      {upload.isSuccess && <div className="ui-alert ui-alert-success">{t("lifecycleCatalog.imported", upload.data)}</div>}
      {upload.error && <div className="ui-alert ui-alert-danger">{errorText(upload.error, "lifecycleCatalog.errors")}</div>}
    </section>
  );
}

export function LifecycleRecordNew() {
  const search = useSearch({ from: "/lifecycle/catalog/new" });
  return <RecordPage prefill={{ vendor: search.vendor ?? "", model: search.model ?? "" }} />;
}

export function LifecycleRecordEdit() {
  const { recordId } = useParams({ from: "/lifecycle/catalog/$recordId/edit" });
  return <RecordPage id={recordId} />;
}

function RecordPage({ id, prefill }: { id?: string; prefill?: { vendor: string; model: string } }) {
  const { t } = useTranslation();
  const query = useCatalogForm(id);
  const data = query.data;
  const title = data?.record?.model ?? t("lifecycleCatalog.newTitle");
  return (
    <section className="ui-page">
      <nav className="ui-breadcrumbs" aria-label={t("device.breadcrumbs")}>
        <Link to="/lifecycle">{t("lifecycle.title")}</Link>
        <span aria-hidden="true">›</span>
        <Link to="/lifecycle/catalog">{t("lifecycleCatalog.title")}</Link>
        <span aria-hidden="true">›</span>
        <span>{title}</span>
      </nav>
      <header className="ui-page-header">
        <h1>{title}</h1>
        <p>{t("lifecycleCatalog.formHelp")}</p>
      </header>
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <div className="ui-empty">{t("app.loading")}</div>
      ) : (
        <>
          <RecordEditor record={data.record} vendors={data.vendors} today={data.today} prefill={prefill} />
          {data.record && <RecordDelete record={data.record} />}
        </>
      )}
    </section>
  );
}

function RecordEditor({ record, vendors, today, prefill }: { record: CatalogRecord | null; vendors: string[]; today: string; prefill?: { vendor: string; model: string } }) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const errorText = useFormError();
  const save = useCatalogWrite<Record<string, string>, { id: string; model: string; linked: number; status_changed: number }>("", (input) => JSON.stringify(input));
  const [form, setForm] = useState({
    vendor: record?.vendor ?? prefill?.vendor ?? "", model: record?.model ?? prefill?.model ?? "", aliases: (record?.aliases ?? []).join(", "),
    eol_date: record?.eol_date ?? "", eos_date: record?.eos_date ?? "", source: record?.source ?? "", source_url: record?.source_url ?? "",
    evidence_date: record?.evidence_date ?? today, notes: record?.notes ?? "",
  });
  const set = (key: keyof typeof form) => (event: { target: { value: string } }) => setForm({ ...form, [key]: event.target.value });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate({ ...form, record_id: record?.id ?? "" }, { onSuccess: () => void navigate({ to: "/lifecycle/catalog" }) });
  };
  return (
    <section className="ui-panel ui-admin-section">
      <form className="ui-admin-form" onSubmit={submit}>
        <label><span>{t("lifecycleCatalog.vendor")}</span>
          <input value={form.vendor} onChange={set("vendor")} required maxLength={60} list="lifecycle-vendors" autoComplete="off" />
          <datalist id="lifecycle-vendors">{vendors.map((vendor) => <option key={vendor} value={vendor} />)}</datalist></label>
        <label className="ui-admin-wide"><span>{t("lifecycleCatalog.model")}</span><input value={form.model} onChange={set("model")} required maxLength={150} autoComplete="off" /></label>
        <label className="ui-admin-wide"><span>{t("lifecycleCatalog.aliases")}</span><input value={form.aliases} onChange={set("aliases")} maxLength={500} />
          <small className="ui-muted">{t("lifecycleCatalog.aliasesHelp")}</small></label>
        <label><span>{t("lifecycleCatalog.eol")}</span><input type="date" value={form.eol_date} onChange={set("eol_date")} /></label>
        <label><span>{t("lifecycleCatalog.eos")}</span><input type="date" value={form.eos_date} onChange={set("eos_date")} /></label>
        <label className="ui-admin-wide"><span>{t("lifecycleCatalog.source")}</span>
          <input value={form.source} onChange={set("source")} required maxLength={160} placeholder={t("lifecycleCatalog.sourcePlaceholder")} /></label>
        <label className="ui-admin-wide"><span>{t("lifecycleCatalog.sourceUrl")}</span><input type="url" value={form.source_url} onChange={set("source_url")} maxLength={500} placeholder="https://" /></label>
        <label><span>{t("lifecycleCatalog.evidence")}</span><input type="date" value={form.evidence_date} max={today} onChange={set("evidence_date")} required /></label>
        <label className="ui-admin-full"><span>{t("lifecycleCatalog.notes")}</span><textarea rows={2} maxLength={2000} value={form.notes} onChange={set("notes")} /></label>
        <div className="ui-admin-full ui-row-actions ui-policy-manage">
          <button type="submit" className="ui-button ui-button-primary" disabled={save.isPending}>{t("lifecycleCatalog.save")}</button>
          <Link to="/lifecycle/catalog" className="ui-button ui-button-ghost">{t("lifecycleCatalog.cancel")}</Link>
          {save.error && <span className="ui-backup-error">{errorText(save.error, "lifecycleCatalog.errors")}</span>}
        </div>
      </form>
      {record?.updated_at && <p className="ui-muted">{t("lifecycleCatalog.updated", { when: formatDateTime(record.updated_at, i18n.language) })}</p>}
    </section>
  );
}

function RecordDelete({ record }: { record: CatalogRecord }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const errorText = useFormError();
  const remove = useCatalogWrite<undefined, { deleted: boolean }>(`/${record.id}/delete`, () => "{}");
  const onDelete = () => {
    if (window.confirm(t("lifecycleCatalog.deleteConfirm", { model: record.model }))) remove.mutate(undefined, { onSuccess: () => void navigate({ to: "/lifecycle/catalog" }) });
  };
  return (
    <section className="ui-panel ui-admin-section ui-danger-zone">
      <h2>{t("lifecycleCatalog.deleteTitle")}</h2>
      <p className="ui-muted">{t("lifecycleCatalog.deleteHelp")}</p>
      <button type="button" className="ui-button ui-button-danger" disabled={remove.isPending} onClick={onDelete}>{t("lifecycleCatalog.delete")}</button>
      {remove.error && <p className="ui-backup-error">{errorText(remove.error, "lifecycleCatalog.errors")}</p>}
    </section>
  );
}
