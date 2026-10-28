import { Link } from "@tanstack/react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type ChangeEvent, type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { useCatalogAction, useVendorCatalogs, type VendorCatalog, type VendorRelease } from "../api/operations";
import { BrandIcon } from "../app/BrandIcon";
import { formatBytes, formatDate } from "../lib/format";
import { useFormError } from "./CustomerForm";
import { Loading } from "../app/States";

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
        <Loading />
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
      {canExecute && <UploadRelease catalog={catalog} />}
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
        {release.model_pattern ? release.model_pattern.split("|").join(", ") : t("firmwareCatalogs.allModels")}
        {release.platform && <small className="ui-muted ui-block">{t("firmwareCatalogs.platform", { platform: release.platform })}</small>}
      </td>
      <td className="ui-nowrap">{formatDate(release.released_at, locale)}</td>
      <td><span className="ui-tag">{t(`firmwareCatalogs.sources.${release.source}`, { defaultValue: release.source })}</span></td>
      <td>{release.security ? <span className="ui-severity ui-severity-high">{t("firmwareCatalogs.securityTag")}</span> : "—"}</td>
      <td className="ui-break">
        {release.file && (
          <a href={`/api/ui/v1/firmware/catalogs/releases/${release.id}/file`} title={`SHA-256 ${release.file.sha256}`} download>
            {t("firmwareCatalogs.upload.download", { name: release.file.name })}
          </a>
        )}
        {release.file && release.file.acs_files.length > 0 && <small className="ui-muted ui-block">{t("firmwareCatalogs.upload.inAcs", { count: release.file.acs_files.length })}</small>}
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

interface Staged {
  token: string;
  original: string;
  image: string;
  size: number;
  detected: { version: string | null; model_pattern: string; platform: string; released_at: string | null; family: string | null };
  missing: ("version" | "models")[];
  models: string[];
  released_default: string;
}

interface Uploaded {
  version: string;
  model_pattern: string | null;
  platform: string | null;
  to_update: number;
  acs_files: string[];
  warnings: string[];
}

/** Upload the vendor image: NSM reads version, models/platform and date from it and asks only what it could not read. */
function UploadRelease({ catalog }: { catalog: VendorCatalog }) {
  const { t, i18n } = useTranslation();
  const errorText = useFormError();
  const client = useQueryClient();
  const [security, setSecurity] = useState(false);
  const [staged, setStaged] = useState<Staged | null>(null);
  const [version, setVersion] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [released, setReleased] = useState("");
  const done = (result: Uploaded) => {
    setStaged(null);
    setSecurity(false);
    void client.invalidateQueries({ queryKey: ["firmware-catalogs"] });
    void client.invalidateQueries({ queryKey: ["firmware"] });
    return result;
  };
  const confirm = useMutation({
    mutationFn: ({ token, body }: { token: string; body: Record<string, unknown> }) =>
      api<Uploaded>(`/api/ui/v1/firmware/catalogs/upload/${token}`, { method: "POST", body: JSON.stringify(body) }).then(done),
  });
  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("brand", catalog.brand);
      form.append("image", file);
      form.append("file_date", new Date(file.lastModified).toISOString().slice(0, 10));
      return api<Staged>("/api/ui/v1/firmware/catalogs/upload", { method: "POST", body: form });
    },
    onSuccess: (data) => {
      confirm.reset();
      if (!data.missing.length) {
        confirm.mutate({ token: data.token, body: { security } });
        return;
      }
      setStaged(data);
      setVersion(data.detected.version ?? "");
      setModels([]);
      setReleased(data.released_default);
    },
  });
  const pick = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) upload.mutate(file);
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!staged) return;
    const body: Record<string, unknown> = { security, released_at: released };
    if (staged.missing.includes("version")) body.version = version;
    if (staged.missing.includes("models")) body.models = models;
    confirm.mutate({ token: staged.token, body });
  };
  const busy = upload.isPending || confirm.isPending;
  return (
    <div className="ui-section-gap ui-firmware-upload">
      <div className="ui-actions">
        <label className={`ui-button ui-button-primary${busy ? " is-disabled" : ""}`}>
          {upload.isPending ? t("firmwareCatalogs.upload.reading") : confirm.isPending ? t("app.loading") : t("firmwareCatalogs.upload.button", { label: catalog.label })}
          <input type="file" accept=".bin,.img,.zip,.tar,.gz,.npk,.pkg" hidden disabled={busy} onChange={pick} />
        </label>
        <label className="ui-with-brand">
          <input type="checkbox" checked={security} onChange={(event) => setSecurity(event.target.checked)} /> <span>{t("firmwareCatalogs.fixesSecurity")}</span>
        </label>
      </div>
      <small className="ui-muted ui-block">{t(`firmwareCatalogs.upload.hint.${catalog.brand}`, { defaultValue: t("firmwareCatalogs.upload.hint.other") })}</small>
      {staged && (
        <form className="ui-admin-form" onSubmit={submit}>
          <p className="ui-admin-wide">
            {t("firmwareCatalogs.upload.staged", { name: staged.image, size: formatBytes(staged.size, i18n.language) })}{" "}
            {staged.detected.version && t("firmwareCatalogs.upload.read", { version: staged.detected.version })}
          </p>
          {staged.missing.includes("version") && (
            <label><span>{t("firmwareCatalogs.version")}</span><input value={version} onChange={(event) => setVersion(event.target.value)} required maxLength={80} placeholder="1.2.3" /></label>
          )}
          {staged.missing.includes("models") && (
            <fieldset className="ui-admin-wide">
              <legend>{t("firmwareCatalogs.upload.models")}</legend>
              {staged.models.map((model) => (
                <label key={model} className="ui-with-brand">
                  <input type="checkbox" checked={models.includes(model)}
                    onChange={(event) => setModels(event.target.checked ? [...models, model] : models.filter((m) => m !== model))} /> <span>{model}</span>
                </label>
              ))}
              <small className="ui-muted ui-block">{t("firmwareCatalogs.upload.modelsHint")}</small>
            </fieldset>
          )}
          <label><span>{t("firmwareCatalogs.released")}</span><input type="date" value={released} onChange={(event) => setReleased(event.target.value)} /></label>
          <div className="ui-actions ui-admin-wide">
            <button type="submit" className="ui-button ui-button-primary" disabled={busy}>{t("firmwareCatalogs.addSubmit")}</button>
            <button type="button" className="ui-button ui-button-ghost" disabled={busy} onClick={() => setStaged(null)}>{t("firmwareCatalogs.upload.cancel")}</button>
          </div>
        </form>
      )}
      {confirm.isSuccess && (
        <div className={confirm.data.warnings.length ? "ui-alert ui-alert-warning" : "ui-alert ui-alert-success"}>
          {t("firmwareCatalogs.added", { version: confirm.data.version, count: confirm.data.to_update })}{" "}
          {confirm.data.model_pattern && t("firmwareCatalogs.upload.for", { models: confirm.data.model_pattern.split("|").join(", ") })}{" "}
          {confirm.data.platform && t("firmwareCatalogs.platform", { platform: confirm.data.platform })}
          {confirm.data.acs_files.length > 0 && <> · {t("firmwareCatalogs.upload.inAcs", { count: confirm.data.acs_files.length })}</>}
          {confirm.data.warnings.map((warning) => <small key={warning} className="ui-block">{warning}</small>)}
        </div>
      )}
      {(upload.error || confirm.error) && <div className="ui-alert ui-alert-danger">{errorText(upload.error ?? confirm.error, "firmwareCatalogs.errors")}</div>}
    </div>
  );
}
