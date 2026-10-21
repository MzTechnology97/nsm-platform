import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { type CreatedDevice, useCustomerWrite, useNewDeviceForm } from "../api/customer";
import { EnrollmentCommand } from "../app/EnrollmentCommand";
import { useFormError } from "./CustomerForm";
import { Loading } from "../app/States";

type Fields = {
  vendor: string;
  device_type: string;
  display_name: string;
  site_id: string;
  mac: string;
  serial: string;
  manufacturer: string;
  model: string;
  management_ip: string;
  firmware_version: string;
};

const EMPTY: Fields = { vendor: "mikrotik", device_type: "router", display_name: "", site_id: "", mac: "", serial: "", manufacturer: "", model: "", management_ip: "", firmware_version: "" };

/** New device of a customer: the vendor decides how NSM associates it (agent, UISP, cnMaestro, TR-069 or manual). */
export function DeviceNew() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const errorText = useFormError();
  const { customerId } = useParams({ from: "/customers/$customerId/devices/new" });
  const query = useNewDeviceForm(customerId);
  const create = useCustomerWrite<Fields, CreatedDevice>(`/${customerId}/devices`);
  const [form, setForm] = useState<Fields>(EMPTY);
  const set = (key: keyof Fields) => (event: { target: { value: string } }) => setForm({ ...form, [key]: event.target.value });
  const data = query.data;

  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <Loading />;

  const created = create.data;
  if (created?.enrollment_command) {
    return (
      <section className="ui-panel ui-admin-section">
        <h2>{t("deviceNew.createdTitle")}</h2>
        <p>{t("deviceNew.createdMikrotik")}</p>
        <EnrollmentCommand command={created.enrollment_command} commandV6={created.enrollment_command_v6} />
        <Link to="/devices/$deviceId" params={{ deviceId: created.id }} className="ui-button">{t("deviceNew.openDevice")}</Link>
      </section>
    );
  }

  const { vendor } = form;
  const connector = vendor === "ubiquiti" ? "uisp" : vendor === "cambium" ? "cnmaestro" : null;
  const connectorMissing = connector !== null && !data.connectors[connector];
  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate(form, {
      onSuccess: (result) => {
        if (!result.enrollment_command) void navigate({ to: "/devices/$deviceId", params: { deviceId: result.id } });
      },
    });
  };

  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("deviceNew.title")}</h2>
      <p className="ui-muted">{t("deviceNew.subtitle")}</p>
      <form className="ui-admin-form ui-admin-stack ui-device-new" onSubmit={submit}>
        <fieldset className="ui-channel">
          <legend>{t("deviceNew.step1")}</legend>
          <label><span>{t("deviceNew.vendor")}</span>
            <select value={vendor} onChange={set("vendor")}>
              {data.vendors.map((key) => <option key={key} value={key}>{t(`deviceNew.vendors.${key}`, { defaultValue: key })}</option>)}
            </select></label>
          <label><span>{t("deviceNew.type")}</span>
            <select value={form.device_type} onChange={set("device_type")}>
              {data.device_types.map((key) => <option key={key} value={key}>{t(`deviceNew.types.${key}`, { defaultValue: key })}</option>)}
            </select></label>
        </fieldset>
        <fieldset className="ui-channel">
          <legend>{t("deviceNew.step2")}</legend>
          <label><span>{t("deviceNew.alias")}</span><input value={form.display_name} onChange={set("display_name")} maxLength={200} autoComplete="off" />
            <small className="ui-muted">{t("deviceNew.aliasHelp")}</small></label>
          <label><span>{t("deviceNew.site")}</span>
            <select value={form.site_id} onChange={set("site_id")}>
              <option value="">{t("deviceNew.noSite")}</option>
              {data.sites.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}
            </select></label>
        </fieldset>
        <fieldset className="ui-channel">
          <legend>{t("deviceNew.step3")}</legend>
          <p className="ui-muted">{t(`deviceNew.how.${vendor}`)}</p>
          {connectorMissing && (
            <div className="ui-alert ui-alert-warning">
              {t(`deviceNew.connectorMissing.${connector}`)}{" "}
              <Link to={connector === "uisp" ? "/admin/integrations/uisp" : "/admin/integrations/cnmaestro"}>{t("deviceNew.configure")}</Link>
            </div>
          )}
          {vendor !== "mikrotik" && (
            <>
              {vendor === "generic" && (
                <label><span>{t("deviceNew.manufacturer")}</span>
                  <select value={form.manufacturer} onChange={set("manufacturer")}>
                    <option value="">{t("deviceNew.otherManufacturer")}</option>
                    {data.manual_brands.map((brand) => <option key={brand.key} value={brand.key}>{brand.label}</option>)}
                  </select>
                  <small className="ui-muted">{t("deviceNew.manufacturerHelp")}</small></label>
              )}
              <label><span>{t("deviceNew.mac")}</span><input value={form.mac} onChange={set("mac")} placeholder="02:00:5E:00:00:01" autoComplete="off" /></label>
              <label><span>{t("deviceNew.serial")}</span><input value={form.serial} onChange={set("serial")} autoComplete="off" />
                {vendor !== "generic" && <small className="ui-muted">{t("deviceNew.macOrSerial")}</small>}</label>
              {vendor === "generic" && (
                <>
                  <label><span>{t("deviceNew.model")}</span><input value={form.model} onChange={set("model")} autoComplete="off" /></label>
                  <label><span>{t("deviceNew.ip")}</span><input value={form.management_ip} onChange={set("management_ip")} placeholder="192.0.2.10" autoComplete="off" /></label>
                  <label><span>{t("deviceNew.firmware")}</span><input value={form.firmware_version} onChange={set("firmware_version")} autoComplete="off" /></label>
                </>
              )}
            </>
          )}
        </fieldset>
        <div className="ui-row-actions">
          <button type="submit" className="ui-button" disabled={create.isPending || connectorMissing}>{t("deviceNew.create")}</button>
          <Link to="/customers/$customerId" params={{ customerId }} className="ui-button ui-button-ghost">{t("customerForm.cancel")}</Link>
        </div>
        {create.error && <small className="ui-backup-error">{errorText(create.error, "deviceNew.errors")}</small>}
      </form>
    </section>
  );
}
