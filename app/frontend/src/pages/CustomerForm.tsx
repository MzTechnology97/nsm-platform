import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import { type CustomerDetail, useCustomer, useCustomerWrite } from "../api/customer";

/** Server validation translated by its code (``customerForm.errors.<code>``). */
export function useFormError() {
  const { t } = useTranslation();
  return (error: unknown) =>
    error instanceof ApiError ? t(`customerForm.errors.${error.code ?? "generic"}`, { defaultValue: error.message }) : error ? String(error) : null;
}

type Fields = { name: string; code: string; notes: string };

function CustomerFields({ form, onChange }: { form: Fields; onChange: (form: Fields) => void }) {
  const { t } = useTranslation();
  const set = (key: keyof Fields) => (event: { target: { value: string } }) => onChange({ ...form, [key]: event.target.value });
  return (
    <>
      <label><span>{t("customerForm.name")}</span><input value={form.name} onChange={set("name")} required maxLength={200} autoComplete="off" /></label>
      <label><span>{t("customerForm.code")}</span><input value={form.code} onChange={set("code")} maxLength={50} autoComplete="off" />
        <small className="ui-muted">{t("customerForm.codeHelp")}</small></label>
      <label><span>{t("customerForm.notes")}</span><textarea value={form.notes} onChange={set("notes")} rows={3} /></label>
    </>
  );
}

/** New customer: name, optional unique code and notes; opens the customer afterwards. */
export function CustomerNew() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const errorText = useFormError();
  const create = useCustomerWrite<Fields>("");
  const [form, setForm] = useState<Fields>({ name: "", code: "", notes: "" });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate(form, { onSuccess: (result) => void navigate({ to: "/customers/$customerId", params: { customerId: result.id } }) });
  };
  return (
    <section className="ui-page">
      <nav className="ui-breadcrumbs" aria-label={t("device.breadcrumbs")}>
        <Link to="/customers">{t("nav.customers")}</Link>
        <span aria-hidden="true">›</span>
        <span>{t("customerForm.newTitle")}</span>
      </nav>
      <header className="ui-page-header">
        <h1>{t("customerForm.newTitle")}</h1>
        <p>{t("customerForm.newSubtitle")}</p>
      </header>
      <section className="ui-panel ui-admin-section ui-form-narrow">
        <form className="ui-admin-form ui-admin-stack" onSubmit={submit}>
          <CustomerFields form={form} onChange={setForm} />
          <div className="ui-row-actions">
            <button type="submit" className="ui-button" disabled={create.isPending}>{t("customerForm.create")}</button>
            <Link to="/customers" className="ui-button ui-button-ghost">{t("customerForm.cancel")}</Link>
          </div>
          {create.error && <small className="ui-backup-error">{errorText(create.error)}</small>}
        </form>
      </section>
    </section>
  );
}

/** Customer edit tab: details form and, separately, deletion confirmed by typing the customer name. */
export function CustomerEdit() {
  const { t } = useTranslation();
  const { customerId } = useParams({ from: "/customers/$customerId/edit" });
  const { data } = useCustomer(customerId);
  if (!data) return null;
  if (!data.actions.edit) return <div className="ui-empty">{t("customerForm.forbidden")}</div>;
  return (
    <div className="ui-two-columns">
      <EditDetails key={data.customer.id} data={data} />
      <DeleteCustomer data={data} />
    </div>
  );
}

function EditDetails({ data }: { data: CustomerDetail }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const errorText = useFormError();
  const { customer } = data;
  const update = useCustomerWrite<Fields>(`/${customer.id}`);
  const [form, setForm] = useState<Fields>({ name: customer.name, code: customer.code ?? "", notes: customer.notes ?? "" });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    update.mutate(form, { onSuccess: () => void navigate({ to: "/customers/$customerId", params: { customerId: customer.id } }) });
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("customerForm.editTitle")}</h2>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit}>
        <CustomerFields form={form} onChange={setForm} />
        <div className="ui-row-actions">
          <button type="submit" className="ui-button" disabled={update.isPending}>{t("customerForm.save")}</button>
          <Link to="/customers/$customerId" params={{ customerId: customer.id }} className="ui-button ui-button-ghost">{t("customerForm.cancel")}</Link>
        </div>
        {update.error && <small className="ui-backup-error">{errorText(update.error)}</small>}
      </form>
    </section>
  );
}

function DeleteCustomer({ data }: { data: CustomerDetail }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const errorText = useFormError();
  const { customer, counts } = data;
  const remove = useCustomerWrite<{ confirm_name: string }, { deleted: boolean }>(`/${customer.id}/delete`, customer.id);
  const [typed, setTyped] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    remove.mutate({ confirm_name: typed }, {
      onSuccess: () => void navigate({ to: "/customers" }),
    });
  };
  return (
    <section className="ui-panel ui-admin-section ui-danger-zone">
      <h2>{t("customerForm.deleteTitle")}</h2>
      <p>{t("customerForm.deleteImpact", { devices: counts.devices, sites: counts.sites })}</p>
      <p className="ui-muted">{t("customerForm.deleteHelp")}</p>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit}>
        <label><span>{t("customerForm.confirmName", { name: customer.name })}</span>
          <input value={typed} onChange={(event) => setTyped(event.target.value)} autoComplete="off" required /></label>
        <button type="submit" className="ui-button ui-button-danger" disabled={remove.isPending || typed.trim() !== customer.name}>{t("customerForm.delete")}</button>
        {remove.error && <small className="ui-backup-error">{errorText(remove.error)}</small>}
      </form>
    </section>
  );
}

type SiteFields = { name: string; address: string; notes: string };

/** Inline site form: creates a site, or edits one and offers its deletion (devices stay in the customer without a site). */
export function SiteForm({ customerId, site, onDone }: {
  customerId: string;
  site?: { id: string; name: string; address: string | null; notes: string | null; devices: number };
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const errorText = useFormError();
  const save = useCustomerWrite<SiteFields>(site ? `/${customerId}/sites/${site.id}` : `/${customerId}/sites`);
  const remove = useCustomerWrite<undefined, { moved: number }>(`/${customerId}/sites/${site?.id ?? ""}/delete`);
  const [form, setForm] = useState<SiteFields>({ name: site?.name ?? "", address: site?.address ?? "", notes: site?.notes ?? "" });
  const [confirming, setConfirming] = useState(false);
  const set = (key: keyof SiteFields) => (event: { target: { value: string } }) => setForm({ ...form, [key]: event.target.value });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(form, { onSuccess: onDone });
  };
  return (
    <form className="ui-admin-form ui-site-form" onSubmit={submit}>
      <label><span>{t("customerForm.siteName")}</span><input value={form.name} onChange={set("name")} required maxLength={200} autoComplete="off" /></label>
      <label className="ui-admin-wide"><span>{t("customerForm.siteAddress")}</span><input value={form.address} onChange={set("address")} maxLength={300} autoComplete="off" /></label>
      <label className="ui-admin-wide"><span>{t("customerForm.notes")}</span><input value={form.notes} onChange={set("notes")} autoComplete="off" /></label>
      <div className="ui-row-actions">
        <button type="submit" className="ui-button ui-button-small" disabled={save.isPending}>{site ? t("customerForm.save") : t("customerForm.addSite")}</button>
        <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={onDone}>{t("customerForm.cancel")}</button>
        {site && !confirming && (
          <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={() => setConfirming(true)}>{t("customerForm.deleteSite")}</button>
        )}
      </div>
      {site && confirming && (
        <div className="ui-alert ui-alert-warning ui-site-delete">
          <span>{t("customerForm.deleteSiteConfirm", { name: site.name, count: site.devices })}</span>
          <div className="ui-row-actions">
            <button type="button" className="ui-button ui-button-danger ui-button-small" disabled={remove.isPending} onClick={() => remove.mutate(undefined, { onSuccess: onDone })}>
              {t("customerForm.deleteSite")}
            </button>
            <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={() => setConfirming(false)}>{t("customerForm.cancel")}</button>
          </div>
        </div>
      )}
      {(save.error || remove.error) && <small className="ui-backup-error">{errorText(save.error ?? remove.error)}</small>}
    </form>
  );
}
