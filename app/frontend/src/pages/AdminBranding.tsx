import { type FormEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { type BrandingAdmin, useBrandingAction, useBrandingAdmin } from "../api/admin";
import { ApiError } from "../api/client";
import { AdminTabs } from "../app/AdminTabs";
import { Loading } from "../app/States";

const COLORS = [
  ["primary", "primary_color"],
  ["sidebar", "sidebar_color"],
  ["highlight", "highlight_color"],
] as const;

function useBrandingError() {
  const { t } = useTranslation();
  return (error: unknown) =>
    error instanceof ApiError && error.code ? t(`branding.errors.${error.code}`, { defaultValue: error.message }) : error ? (error as Error).message : null;
}

export function AdminBranding() {
  const { t } = useTranslation();
  const query = useBrandingAdmin();
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("admin.title")}</h1>
          <p>{t("branding.subtitle")}</p>
        </div>
      </header>
      <AdminTabs />
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : (
        <div className="ui-two-columns">
          <Identity key={`${data.primary_color}${data.sidebar_color}${data.highlight_color}`} data={data} />
          <Logo data={data} />
        </div>
      )}
    </section>
  );
}

function Identity({ data }: { data: BrandingAdmin }) {
  const { t } = useTranslation();
  const errorText = useBrandingError();
  const [form, setForm] = useState({
    portal_name: data.portal_name, tagline: data.tagline, primary_color: data.primary_color, sidebar_color: data.sidebar_color, highlight_color: data.highlight_color,
  });
  const save = useBrandingAction<typeof form>("", (value) => JSON.stringify(value));
  const [resetting, setResetting] = useState<string | null>(null);
  const resetOne = useResetColor(setResetting);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(form);
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("branding.identity")}</h2>
      <p className="ui-muted">{t("branding.identityHelp")}</p>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit}>
        <label><span>{t("branding.name")}</span><input value={form.portal_name} maxLength={160} required onChange={(event) => setForm({ ...form, portal_name: event.target.value })} /></label>
        <label><span>{t("branding.tagline")}</span><input value={form.tagline} maxLength={240} placeholder="Network operations & security management" onChange={(event) => setForm({ ...form, tagline: event.target.value })} /></label>
        {COLORS.map(([key, field]) => (
          <label key={key}>
            <span>{t(`branding.colors.${key}`)}</span>
            <span className="ui-color-control">
              <input type="color" value={form[field]} onChange={(event) => setForm({ ...form, [field]: event.target.value })} aria-label={t(`branding.colors.${key}`)} />
              <code>{form[field]}</code>
              <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={resetting === key} onClick={() => resetOne(key)}>{t("branding.default")}</button>
            </span>
            <small className="ui-muted">{t(`branding.colorHelp.${key}`, { value: data.defaults[key] })}</small>
          </label>
        ))}
        <button type="submit" className="ui-button" disabled={save.isPending}>{t("branding.save")}</button>
        {save.isSuccess && <small className="ui-live">{t("branding.saved")}</small>}
        {save.error && <small className="ui-backup-error">{errorText(save.error)}</small>}
      </form>
    </section>
  );
}

/** Reset one colour to its default; the page reloads its data from the response. */
function useResetColor(setBusy: (key: string | null) => void) {
  const primary = useBrandingAction<null>("/colors/primary/reset", () => undefined);
  const sidebar = useBrandingAction<null>("/colors/sidebar/reset", () => undefined);
  const highlight = useBrandingAction<null>("/colors/highlight/reset", () => undefined);
  const actions = { primary, sidebar, highlight } as const;
  return (key: string) => {
    setBusy(key);
    actions[key as keyof typeof actions].mutate(null, { onSettled: () => setBusy(null) });
  };
}

function Logo({ data }: { data: BrandingAdmin }) {
  const { t } = useTranslation();
  const errorText = useBrandingError();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const upload = useBrandingAction<File>("/logo", (value) => {
    const body = new FormData();
    body.append("logo", value);
    return body;
  });
  const remove = useBrandingAction<null>("/logo/remove", () => undefined);
  const tooBig = file !== null && file.size > data.max_logo_bytes;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (file && !tooBig) upload.mutate(file, { onSuccess: () => { setFile(null); if (input.current) input.current.value = ""; } });
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("branding.logo")}</h2>
      <p className="ui-muted">{t("branding.logoHelp")}</p>
      <div className="ui-logo-preview">
        {data.logo_url ? <img src={data.logo_url} alt={t("branding.currentLogo")} /> : <strong>NSM</strong>}
      </div>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit}>
        <label>
          <span>{t("branding.newLogo")}</span>
          <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
        </label>
        {tooBig && <small className="ui-backup-error">{t("branding.errors.logo_size")}</small>}
        <button type="submit" className="ui-button" disabled={!file || tooBig || upload.isPending}>{t("branding.upload")}</button>
        {upload.isSuccess && <small className="ui-live">{t("branding.logoSaved")}</small>}
        {upload.error && <small className="ui-backup-error">{errorText(upload.error)}</small>}
      </form>
      {data.has_logo && (
        <div className="ui-admin-form">
          <button type="button" className="ui-button ui-button-ghost" disabled={remove.isPending}
            onClick={() => { if (window.confirm(t("branding.confirmRemove"))) remove.mutate(null); }}>
            {t("branding.remove")}
          </button>
          {remove.isSuccess && <small className="ui-live">{t("branding.logoRemoved")}</small>}
        </div>
      )}
      <p className="ui-muted">{t("branding.appliesTo")}</p>
    </section>
  );
}
