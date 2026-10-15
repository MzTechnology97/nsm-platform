import { Link } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { useCatalogAction, useVendorCatalogs, type VendorCatalog, type VendorRelease } from "../api/operations";
import { BrandIcon } from "../app/BrandIcon";
import { formatDate } from "../lib/format";
import { useFormError } from "./CustomerForm";

/** One firmware catalog per non-MikroTik brand in the inventory: UISP, the official update service, or operator releases. */
export function FirmwareCatalogs() {
  const { t } = useTranslation();
  const query = useVendorCatalogs();
  const errorText = useFormError();
  const refresh = useCatalogAction<undefined, { uisp: number; vendor_api: number; platforms: number; errors: string[]; evaluated: number }>("/refresh");
  const data = query.data;

  return (
    <section className="ui-page">
      <nav className="ui-breadcrumbs" aria-label={t("device.breadcrumbs")}>
        <Link to="/firmware">{t("firmware.title")}</Link>
        <span aria-hidden="true">›</span>
        <span>{t("firmwareCatalogs.title")}</span>
      </nav>
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("firmwareCatalogs.title")}</h1>
          <p>{t("firmwareCatalogs.subtitle")} <Link to="/integrations/routeros">{t("firmwareCatalogs.routeros")}</Link></p>
        </div>
        {data?.can_execute && (
          <div className="ui-actions">
            <button type="button" className="ui-button" disabled={refresh.isPending} onClick={() => refresh.mutate(undefined)}>
              {refresh.isPending ? t("app.loading") : t("firmwareCatalogs.refresh")}
            </button>
          </div>
        )}
      </header>
      {refresh.isSuccess && (
        <div className={refresh.data.errors.length ? "ui-alert ui-alert-warning" : "ui-alert ui-alert-success"}>
          {t("firmwareCatalogs.refreshed", refresh.data)}
          {refresh.data.errors.length > 0 && <> {t("firmwareCatalogs.unreachable", { list: refresh.data.errors.join(", ") })}</>}
        </div>
      )}
      {refresh.error && <div className="ui-alert ui-alert-danger">{errorText(refresh.error)}</div>}

      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <div className="ui-empty">{t("app.loading")}</div>
      ) : data.catalogs.length ? (
        data.catalogs.map((catalog) => <Catalog key={catalog.brand} catalog={catalog} canExecute={data.can_execute} />)
      ) : (
        <div className="ui-empty">{t("firmwareCatalogs.none")}</div>
      )}
    </section>
  );
}

function Catalog({ catalog, canExecute }: { catalog: VendorCatalog; canExecute: boolean }) {
  const { t, i18n } = useTranslation();
  return (
    <section className="ui-panel ui-admin-section" id={`catalog-${catalog.brand}`}>
      <div className="ui-panel-header">
        <div>
          <h2 className="ui-with-brand"><BrandIcon brand={catalog.icon} /> {catalog.label}</h2>
          <p className="ui-muted">
            {t("firmwareCatalogs.summary", { devices: catalog.devices, outdated: catalog.outdated })}
            {catalog.security > 0 && <> {t("firmwareCatalogs.securitySummary", { count: catalog.security })}</>}
            {catalog.unknown_version > 0 && <> · {t("firmwareCatalogs.unknownVersion", { count: catalog.unknown_version })}</>}
            {catalog.page && <> · <a href={catalog.page} target="_blank" rel="noopener noreferrer">{t("firmwareCatalogs.officialPage")}</a></>}
          </p>
        </div>
        <Link to="/firmware" search={{ vendor: catalog.brand }} className="ui-button ui-button-ghost">{t("firmwareCatalogs.devices")}</Link>
      </div>
      {catalog.releases.length ? (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead>
              <tr>{["version", "models", "released", "source", "security", "notes", "actions"].map((key) => <th key={key}><span className="ui-th">{t(`firmwareCatalogs.columns.${key}`)}</span></th>)}</tr>
            </thead>
            <tbody>
              {catalog.releases.map((release) => <ReleaseRow key={release.id} release={release} canExecute={canExecute} locale={i18n.language} />)}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ui-empty">{catalog.brand === "ubiquiti" ? t("firmwareCatalogs.emptyUbiquiti", { label: catalog.label }) : t("firmwareCatalogs.empty", { label: catalog.label })}</div>
      )}
      {canExecute && <AddRelease catalog={catalog} />}
    </section>
  );
}

function ReleaseRow({ release, canExecute, locale }: { release: VendorRelease; canExecute: boolean; locale: string }) {
  const { t } = useTranslation();
  const errorText = useFormError();
  const remove = useCatalogAction<undefined, { deleted: boolean }>(`/releases/${release.id}/delete`);
  const onDelete = () => {
    if (window.confirm(t("firmwareCatalogs.deleteConfirm", { version: release.version }))) remove.mutate(undefined);
  };
  return (
    <tr>
      <td><strong className="ui-mono">{release.version}</strong></td>
      <td>
        {release.model_pattern ?? t("firmwareCatalogs.allModels")}
        {release.platform && <small className="ui-muted ui-block">{t("firmwareCatalogs.platform", { platform: release.platform })}</small>}
      </td>
      <td className="ui-nowrap">{formatDate(release.released_at, locale)}</td>
      <td><span className="ui-tag">{t(`firmwareCatalogs.sources.${release.source}`, { defaultValue: release.source })}</span></td>
      <td>{release.security ? <span className="ui-severity ui-severity-high">{t("firmwareCatalogs.securityTag")}</span> : "—"}</td>
      <td className="ui-break">
        {release.notes_url && <a href={release.notes_url} target="_blank" rel="noopener noreferrer">{t("firmwareCatalogs.releaseNotes")}</a>}
        {release.notes && <small className="ui-muted ui-block">{release.notes}</small>}
      </td>
      <td>
        {release.source === "operator" && canExecute && (
          <button type="button" className="ui-button ui-button-ghost ui-button-small ui-button-danger" disabled={remove.isPending} onClick={onDelete}>{t("firmwareCatalogs.delete")}</button>
        )}
        {remove.error && <small className="ui-backup-error ui-block">{errorText(remove.error, "firmwareCatalogs.errors")}</small>}
      </td>
    </tr>
  );
}

const EMPTY = { version: "", model_pattern: "", released_at: "", notes_url: "", security: false, notes: "" };

function AddRelease({ catalog }: { catalog: VendorCatalog }) {
  const { t } = useTranslation();
  const errorText = useFormError();
  const add = useCatalogAction<typeof EMPTY & { brand: string }, { version: string; to_update: number }>("/releases");
  const [form, setForm] = useState(EMPTY);
  const set = (key: keyof typeof EMPTY) => (event: { target: { value: string } }) => setForm({ ...form, [key]: event.target.value });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    add.mutate({ ...form, brand: catalog.brand }, { onSuccess: () => setForm(EMPTY) });
  };
  return (
    <details className="ui-section-gap">
      <summary>{t("firmwareCatalogs.add", { label: catalog.label })}</summary>
      <form className="ui-admin-form" onSubmit={submit}>
        <label><span>{t("firmwareCatalogs.version")}</span><input value={form.version} onChange={set("version")} required maxLength={80} placeholder="1.2.3" /></label>
        <label className="ui-admin-wide"><span>{t("firmwareCatalogs.models")}</span><input value={form.model_pattern} onChange={set("model_pattern")} maxLength={120} placeholder={t("firmwareCatalogs.modelsPlaceholder")} /></label>
        <label><span>{t("firmwareCatalogs.released")}</span><input type="date" value={form.released_at} onChange={set("released_at")} /></label>
        <label className="ui-admin-wide"><span>{t("firmwareCatalogs.notesUrl")}</span><input type="url" value={form.notes_url} onChange={set("notes_url")} maxLength={500} placeholder="https://" /></label>
        <label className="ui-admin-wide"><span>{t("firmwareCatalogs.notes")}</span><input value={form.notes} onChange={set("notes")} maxLength={2000} /></label>
        <label className="ui-with-brand"><input type="checkbox" checked={form.security} onChange={(event) => setForm({ ...form, security: event.target.checked })} /> <span>{t("firmwareCatalogs.fixesSecurity")}</span></label>
        <button type="submit" className="ui-button ui-button-primary" disabled={add.isPending}>{t("firmwareCatalogs.addSubmit")}</button>
      </form>
      {add.isSuccess && <div className="ui-alert ui-alert-success">{t("firmwareCatalogs.added", { version: add.data.version, count: add.data.to_update })}</div>}
      {add.error && <div className="ui-alert ui-alert-danger">{errorText(add.error, "firmwareCatalogs.errors")}</div>}
    </details>
  );
}
