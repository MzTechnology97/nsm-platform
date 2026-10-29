import { Link } from "@tanstack/react-router";
import { type FormEvent, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { type UnifiAdmin, useConnectorAction, useUnifiAdmin } from "../api/admin";
import { AdminTabs } from "../app/AdminTabs";
import { BrandIcon } from "../app/BrandIcon";
import { IntegrationTabs } from "../app/IntegrationTabs";
import { formatDateTime } from "../lib/format";
import { Loading } from "../app/States";

export function AdminUnifi() {
  const { t } = useTranslation();
  const query = useUnifiAdmin();
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("unifi.title")}</h1>
          <p>{t("unifi.subtitle")}</p>
        </div>
        <div className="ui-row-actions">
          <Link className="ui-button ui-button-ghost" to="/integrations">{t("cnmaestro.allIntegrations")}</Link>
        </div>
      </header>
      <AdminTabs />
      <IntegrationTabs />
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : (
        <>
          <Status data={data} />
          <div className="ui-two-columns">
            <Connection data={data} />
            <Actions data={data} />
          </div>
          {data.configured && <Devices data={data} />}
        </>
      )}
    </section>
  );
}

function Status({ data }: { data: UnifiAdmin }) {
  const { t, i18n } = useTranslation();
  const last = data.last_sync;
  return (
    <>
      <div className="ui-metrics">
        <div className="ui-metric">
          <span>{t("unifi.lastSync")}</span>
          <strong>{data.last_sync_at ? formatDateTime(data.last_sync_at, i18n.language) : "—"}</strong>
          <small>{last.status === "success" ? t("cnmaestro.syncOk") : last.status === "failed" ? t("cnmaestro.syncFailed") : t("cnmaestro.syncAuto")}</small>
        </div>
        <div className="ui-metric"><span>{t("unifi.controller")}</span><strong>{last.version ?? "—"}</strong><small>UniFi Network · {t("unifi.sites", { count: last.sites ?? 0 })}</small></div>
        <div className="ui-metric"><span>{t("unifi.inController")}</span><strong>{last.unifi_devices ?? "—"}</strong><small>{t("unifi.notInNsm", { count: last.not_in_nsm ?? 0 })}</small></div>
        <div className="ui-metric"><span>{t("unifi.linked")}</span><strong>{last.linked ?? 0}</strong><small>{t("unifi.byMac")}</small></div>
      </div>
      {last.statistics_error && <div className="ui-alert ui-alert-warning">{last.statistics_error}</div>}
      {data.last_test_status === "failed" && data.last_error && <div className="ui-alert ui-alert-warning">{data.last_error}</div>}
    </>
  );
}

function Connection({ data }: { data: UnifiAdmin }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({
    base_url: data.base_url, api_key: "", verify_tls: data.verify_tls, enabled: data.configured ? data.enabled : true, sites: data.sites,
  });
  const save = useConnectorAction<typeof form>("unifi", "");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(form, { onSuccess: () => setForm({ ...form, api_key: "" }) });
  };
  const toggleSite = (id: string, on: boolean) => setForm({ ...form, sites: on ? [...form.sites, id] : form.sites.filter((site) => site !== id) });
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("cnmaestro.connection")}</h2>
        <span className={`ui-status ui-status-${data.enabled ? "online" : "offline"}`}>{data.enabled ? t("cnmaestro.active") : t("cnmaestro.inactive")}</span>
      </div>
      <p className="ui-muted">{t("unifi.connectionHelp")}</p>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit} autoComplete="off">
        <label><span>{t("unifi.url")}</span><input type="url" required value={form.base_url} placeholder="https://unifi.example.net" onChange={(event) => setForm({ ...form, base_url: event.target.value })} /></label>
        <label>
          <span>{t("unifi.apiKey")}</span>
          <input type="password" value={form.api_key} autoComplete="off" placeholder={data.configured ? t("notificationsAdmin.keepSecret") : ""}
            onChange={(event) => setForm({ ...form, api_key: event.target.value })} />
        </label>
        <label className="ui-with-brand"><input type="checkbox" checked={form.verify_tls} onChange={(event) => setForm({ ...form, verify_tls: event.target.checked })} /> <span>{t("cnmaestro.verifyTls")}</span></label>
        <small className="ui-muted">{t("unifi.tlsHelp")}</small>
        {data.known_sites.length > 0 && (
          <fieldset className="ui-admin-stack">
            <legend>{t("unifi.siteFilter")}</legend>
            <small className="ui-muted">{t("unifi.siteFilterHelp")}</small>
            {data.known_sites.map((site) => (
              <label key={site.id} className="ui-with-brand">
                <input type="checkbox" checked={form.sites.includes(site.id)} onChange={(event) => toggleSite(site.id, event.target.checked)} /> <span>{site.name || site.id}</span>
              </label>
            ))}
          </fieldset>
        )}
        <label className="ui-with-brand"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /> <span>{t("cnmaestro.enabled")}</span></label>
        <button type="submit" className="ui-button" disabled={save.isPending}>{t("cnmaestro.save")}</button>
        {save.isSuccess && <small className="ui-live">{t("unifi.saved")}</small>}
        {save.error && <small className="ui-backup-error">{(save.error as Error).message}</small>}
      </form>
    </section>
  );
}

function Actions({ data }: { data: UnifiAdmin }) {
  const { t } = useTranslation();
  const test = useConnectorAction<object, { ok: boolean; message: string }>("unifi", "/test");
  const sync = useConnectorAction<object, { status: string; error: string | null; version: string | null; sites: number; unifi_devices: number; linked: number; new_links: number; not_in_nsm: number }>("unifi", "/sync");
  const syncMessage = sync.data
    ? sync.data.status === "failed" ? sync.data.error : sync.data.status === "disabled" ? t("unifi.disabledConnector")
      : t("unifi.synced", { devices: sync.data.unifi_devices, sites: sync.data.sites, linked: sync.data.linked, created: sync.data.new_links, missing: sync.data.not_in_nsm })
    : null;
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("cnmaestro.actions")}</h2>
      <p className="ui-muted">{t("unifi.actionsHelp")}</p>
      <div className="ui-row-actions">
        <button type="button" className="ui-button ui-button-ghost" disabled={!data.configured || test.isPending} onClick={() => test.mutate({})}>{t("cnmaestro.test")}</button>
        <button type="button" className="ui-button" disabled={!data.configured || sync.isPending} onClick={() => sync.mutate({})}>{sync.isPending ? t("app.loading") : t("cnmaestro.syncNow")}</button>
      </div>
      {test.data && <p className={test.data.ok ? "ui-live" : "ui-backup-error"}>{test.data.message}</p>}
      {syncMessage && <p className={sync.data?.status === "success" ? "ui-live" : "ui-backup-error"}>{syncMessage}</p>}
      {(test.error || sync.error) && <p className="ui-backup-error">{((test.error ?? sync.error) as Error).message}</p>}
    </section>
  );
}

function Devices({ data }: { data: UnifiAdmin }) {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  const rows = useMemo(() => {
    const needle = text.trim().toLowerCase();
    return needle ? data.devices.filter((d) => [d.name, d.model, d.ip, d.mac, d.site].some((v) => (v ?? "").toLowerCase().includes(needle))) : data.devices;
  }, [data.devices, text]);
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <h2>{t("unifi.devicesTitle")}</h2>
        <input type="search" value={text} placeholder={t("unifi.filter")} onChange={(event) => setText(event.target.value)} />
      </div>
      <p className="ui-muted">{t("unifi.devicesHelp")}</p>
      {data.devices.length === 0 ? (
        <p className="ui-muted">{t("unifi.noDevices")}</p>
      ) : (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead><tr>{["site", "device", "type", "ip", "firmware", "state", "nsm"].map((key) => <th key={key}><span className="ui-th">{t(`unifi.columns.${key}`)}</span></th>)}</tr></thead>
            <tbody>
              {rows.map((device) => (
                <tr key={device.id ?? device.mac ?? device.name}>
                  <td>{device.site ?? "—"}</td>
                  <td>
                    <div className="ui-with-brand">
                      <BrandIcon brand={data.brand} small />
                      <span>{device.name ?? "—"}<small className="ui-muted ui-block">{[device.model, device.mac].filter(Boolean).join(" · ")}</small></span>
                    </div>
                  </td>
                  <td>{t(`deviceNew.types.${device.kind}`, { defaultValue: device.kind })}</td>
                  <td>{device.ip ? <code>{device.ip}</code> : "—"}</td>
                  <td>{device.firmware ?? "—"}{device.firmware_updatable && <small className="ui-muted ui-block">{t("unifi.updateAvailable")}</small>}</td>
                  <td><span className={`ui-status ui-status-${device.status ?? "unknown"}`}>{device.state ?? "—"}</span></td>
                  <td>{device.in_nsm ? t("unifi.inNsm") : t("unifi.missing")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
