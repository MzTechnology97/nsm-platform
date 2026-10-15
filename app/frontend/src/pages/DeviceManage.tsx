import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { type ManagedDevice, type MoveTarget, useCustomerDevicesManage, useDeviceManage, useDeviceWrite } from "../api/customer";
import { BrandIcon } from "../app/BrandIcon";
import { useFormError } from "./CustomerForm";

/** Customer and site pickers of a move: only the chosen customer's sites are offered. */
function TargetFields({ targets, customerId, siteId, onChange }: {
  targets: MoveTarget[];
  customerId: string;
  siteId: string;
  onChange: (customerId: string, siteId: string) => void;
}) {
  const { t } = useTranslation();
  const sites = targets.find((target) => target.id === customerId)?.sites ?? [];
  return (
    <>
      <label><span>{t("deviceManage.customer")}</span>
        <select value={customerId} onChange={(event) => onChange(event.target.value, "")} required>
          <option value="" disabled>{t("deviceManage.chooseCustomer")}</option>
          {targets.map((target) => <option key={target.id} value={target.id}>{target.name}</option>)}
        </select></label>
      <label><span>{t("deviceManage.site")}</span>
        <select value={siteId} onChange={(event) => onChange(customerId, event.target.value)}>
          <option value="">{t("deviceNew.noSite")}</option>
          {sites.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}
        </select></label>
    </>
  );
}

/** Device management tab: alias, customer and site; deletion confirmed by typing the device name. */
export function DeviceManage() {
  const { t } = useTranslation();
  const { deviceId } = useParams({ from: "/devices/$deviceId/manage" });
  const query = useDeviceManage(deviceId);
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!query.data) return <div className="ui-empty">{t("app.loading")}</div>;
  return (
    <div className="ui-two-columns">
      <EditDevice key={query.data.device.id} device={query.data.device} targets={query.data.targets} />
      <DeleteDevice device={query.data.device} />
    </div>
  );
}

function EditDevice({ device, targets }: { device: ManagedDevice; targets: MoveTarget[] }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const errorText = useFormError();
  const save = useDeviceWrite<{ display_name: string; customer_id: string; site_id: string }>(`/devices/${device.id}`);
  const [form, setForm] = useState({ display_name: device.display_name ?? "", customer_id: device.customer_id, site_id: device.site?.id ?? "" });
  const moving = form.customer_id !== device.customer_id;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(form, { onSuccess: () => void navigate({ to: "/devices/$deviceId", params: { deviceId: device.id } }) });
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("deviceManage.title")}</h2>
      <p className="ui-muted">{t("deviceManage.help")}</p>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit}>
        <label><span>{t("deviceNew.alias")}</span>
          <input value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} maxLength={200} autoComplete="off" />
          <small className="ui-muted">{device.identity ? t("deviceManage.identity", { identity: device.identity }) : t("deviceNew.aliasHelp")}</small></label>
        <TargetFields targets={targets} customerId={form.customer_id} siteId={form.site_id}
          onChange={(customerId, siteId) => setForm({ ...form, customer_id: customerId, site_id: siteId })} />
        {moving && <div className="ui-alert ui-alert-warning">{t("deviceManage.moveWarning")}</div>}
        <div className="ui-row-actions">
          <button type="submit" className="ui-button" disabled={save.isPending}>{t("customerForm.save")}</button>
          <Link to="/devices/$deviceId" params={{ deviceId: device.id }} className="ui-button ui-button-ghost">{t("customerForm.cancel")}</Link>
        </div>
        {save.error && <small className="ui-backup-error">{errorText(save.error, "deviceManage.errors")}</small>}
      </form>
    </section>
  );
}

function DeleteDevice({ device }: { device: ManagedDevice }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const errorText = useFormError();
  const remove = useDeviceWrite<{ confirm_name: string }, { customer_id: string }>(`/devices/${device.id}/delete`, device.id);
  const [typed, setTyped] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    remove.mutate({ confirm_name: typed }, { onSuccess: (result) => void navigate({ to: "/customers/$customerId", params: { customerId: result.customer_id } }) });
  };
  return (
    <section className="ui-panel ui-admin-section ui-danger-zone">
      <h2>{t("deviceManage.deleteTitle")}</h2>
      <p className="ui-muted">{t("deviceManage.deleteHelp")}</p>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit}>
        <label><span>{t("customerForm.confirmName", { name: device.name })}</span>
          <input value={typed} onChange={(event) => setTyped(event.target.value)} autoComplete="off" required /></label>
        <button type="submit" className="ui-button ui-button-danger" disabled={remove.isPending || typed.trim() !== device.name}>{t("customerForm.delete")}</button>
        {remove.error && <small className="ui-backup-error">{errorText(remove.error, "deviceManage.errors")}</small>}
      </form>
    </section>
  );
}

/** Customer devices management: select several devices, then move them or delete them (confirmed with their number). */
export function CustomerDevicesManage() {
  const { t } = useTranslation();
  const errorText = useFormError();
  const { customerId } = useParams({ from: "/customers/$customerId/devices/manage" });
  const query = useCustomerDevicesManage(customerId);
  const bulk = useDeviceWrite<object, { changed: number }>(`/customers/${customerId}/devices/bulk`);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [action, setAction] = useState<"move" | "delete">("move");
  const [target, setTarget] = useState({ customer_id: customerId, site_id: "" });
  const [confirm, setConfirm] = useState("");
  const [text, setText] = useState("");
  const data = query.data;
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <div className="ui-empty">{t("app.loading")}</div>;

  const term = text.trim().toLowerCase();
  const rows = term
    ? data.devices.filter((d) => [d.name, d.model, d.management_ip, d.primary_mac, d.site?.name].some((value) => (value ?? "").toLowerCase().includes(term)))
    : data.devices;
  const allShown = rows.length > 0 && rows.every((d) => selected.has(d.id));
  const toggle = (id: string) => setSelected((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });
  const toggleAll = () => setSelected((current) => {
    const next = new Set(current);
    for (const d of rows) {
      if (allShown) next.delete(d.id);
      else next.add(d.id);
    }
    return next;
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    bulk.mutate(
      { action, device_ids: [...selected], target_customer_id: target.customer_id, target_site_id: target.site_id, confirm },
      { onSuccess: () => { setSelected(new Set()); setConfirm(""); } },
    );
  };

  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <div>
          <h2>{t("deviceManage.bulkTitle")}</h2>
          <p className="ui-muted">{t("deviceManage.bulkHelp")}</p>
        </div>
        <Link to="/customers/$customerId/devices/new" params={{ customerId }} className="ui-button ui-button-ghost">{t("customer.actions.addDevice")}</Link>
      </div>
      <div className="ui-filter-bar">
        <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder={t("deviceManage.search")} aria-label={t("deviceManage.search")} autoComplete="off" />
      </div>
      {rows.length ? (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead>
              <tr>
                <th><input type="checkbox" checked={allShown} onChange={toggleAll} aria-label={t("deviceManage.selectAll")} /></th>
                {["device", "site", "address", "status"].map((key) => <th key={key}><span className="ui-th">{t(`deviceManage.columns.${key}`)}</span></th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((device) => (
                <tr key={device.id} className={selected.has(device.id) ? "is-selected" : undefined}>
                  <td><input type="checkbox" checked={selected.has(device.id)} onChange={() => toggle(device.id)} aria-label={device.name} /></td>
                  <td>
                    <span className="ui-with-brand">
                      <BrandIcon brand={device.brand} small />
                      <Link to="/devices/$deviceId" params={{ deviceId: device.id }} className="ui-cell-title">{device.name}</Link>
                    </span>
                    {device.model && <small className="ui-muted ui-block">{device.model}</small>}
                  </td>
                  <td>{device.site?.name ?? <span className="ui-muted">{t("deviceNew.noSite")}</span>}</td>
                  <td className="ui-mono">{device.management_ip ?? device.primary_mac ?? "—"}</td>
                  <td><span className={`ui-status ui-status-${device.status}`}>{t(`devices.statusLabel.${device.status}`, { defaultValue: device.status })}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ui-empty">{t("deviceManage.empty")}</div>
      )}
      <form className="ui-admin-form ui-bulk-bar" onSubmit={submit}>
        <strong>{t("deviceManage.selected", { count: selected.size })}</strong>
        <label><span>{t("deviceManage.action")}</span>
          <select value={action} onChange={(event) => setAction(event.target.value as "move" | "delete")}>
            <option value="move">{t("deviceManage.move")}</option>
            <option value="delete">{t("deviceManage.delete")}</option>
          </select></label>
        {action === "move" ? (
          <TargetFields targets={data.targets} customerId={target.customer_id} siteId={target.site_id}
            onChange={(customer, site) => setTarget({ customer_id: customer, site_id: site })} />
        ) : (
          <label><span>{t("deviceManage.confirmCount", { count: selected.size })}</span>
            <input value={confirm} onChange={(event) => setConfirm(event.target.value)} inputMode="numeric" autoComplete="off" required /></label>
        )}
        <button type="submit" className={`ui-button${action === "delete" ? " ui-button-danger" : ""}`}
          disabled={bulk.isPending || selected.size === 0 || (action === "delete" && confirm !== String(selected.size))}>
          {t("deviceManage.apply")}
        </button>
        {bulk.isSuccess && <small className="ui-live">{t("deviceManage.done", { count: bulk.data.changed })}</small>}
        {bulk.error && <small className="ui-backup-error">{errorText(bulk.error, "deviceManage.errors")}</small>}
      </form>
    </section>
  );
}
