import { Link, useParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import { type GenieacsCandidate, useDeviceCnmaestro, useDeviceGenieacs, useGenieacsAction } from "../api/deviceConnectors";
import { formatDateTime } from "../lib/format";
import { metricValue } from "./DeviceUisp";
import { Loading } from "../app/States";
import { MonitorLink } from "../app/MonitorLink";
import { AcsDiagnostics } from "../app/AcsDiagnostics";
import { AcsSpeedtest } from "../app/AcsSpeedtest";
import { AcsWifi } from "../app/AcsWifi";
import { AcsSnapshot } from "../app/AcsSnapshot";
import { AcsPortMapping } from "../app/AcsPortMapping";
import { AcsMonitor } from "../app/AcsMonitor";
import { TplinkAccess } from "../app/TplinkAccess";
import { TplinkSettings } from "../app/TplinkSettings";

function message(error: unknown) {
  return error instanceof ApiError ? error.message : (error as Error | null)?.message;
}

/** cnMaestro page of a Cambium device: link made by the cnMaestro sync, last values and their history. */
export function DeviceCnmaestro() {
  const { t, i18n } = useTranslation();
  const { deviceId } = useParams({ from: "/devices/$deviceId/cnmaestro" });
  const query = useDeviceCnmaestro(deviceId);
  const data = query.data;
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <Loading />;
  const meta = data.meta;
  return (
    <>
      <section className="ui-panel ui-admin-section">
        <h2>cnMaestro</h2>
        <p className="ui-muted">
          {data.linked
            ? t("deviceCnmaestro.linked", { when: formatDateTime(meta.linked_at, i18n.language), mac: meta.mac })
            : t("deviceCnmaestro.notLinked", { mac: data.record.mac ?? t("deviceCnmaestro.missing"), serial: data.record.serial ?? t("deviceCnmaestro.missing") })}
          {meta.last_sync_at && ` ${t("deviceCnmaestro.lastSync", { when: formatDateTime(meta.last_sync_at, i18n.language) })}`}
        </p>
        {!data.configured && (
          <div className="ui-alert ui-alert-warning">{t("deviceCnmaestro.notConfigured")}{" "}
            {data.can.configure && <Link to="/admin/integrations/cnmaestro">{t("deviceUisp.configure")}</Link>}</div>
        )}
        {data.linked && (
          <>
            <dl className="ui-dl">
              {(["name", "type", "network", "tower", "status"] as const).map((key) => (
                <div key={key}><dt>{t(`deviceCnmaestro.fields.${key}`)}</dt><dd>{meta[key] || "—"}</dd></div>
              ))}
            </dl>
            <div className="ui-data-wrap">
              <table className="ui-data-table">
                <thead><tr><th><span className="ui-th">{t("deviceUisp.metricColumns.metric")}</span></th><th><span className="ui-th">{t("deviceCnmaestro.lastValue")}</span></th></tr></thead>
                <tbody>
                  {data.metrics.map((row) => (
                    <tr key={row.field}>
                      <td>{t(`deviceCnmaestro.series.${row.field}`)}</td>
                      <td>{row.value == null ? <span className="ui-muted">{t("deviceCnmaestro.notExposed")}</span> : metricValue(row.field, row.unit, row.value, i18n.language)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
      {data.linked && data.can.metrics && (
        <MonitorLink deviceId={deviceId} />
      )}
    </>
  );
}

/** GenieACS page of a TR-069 CPE: lookup by serial then MAC, association and refresh; customer and site always stay those of NSM. */
export function DeviceGenieacs() {
  const { t, i18n } = useTranslation();
  const { deviceId } = useParams({ from: "/devices/$deviceId/genieacs" });
  const query = useDeviceGenieacs(deviceId);
  const preview = useGenieacsAction<GenieacsCandidate>(deviceId, "preview");
  const associate = useGenieacsAction(deviceId, "associate");
  const refresh = useGenieacsAction(deviceId, "refresh");
  const data = query.data;
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <Loading />;
  const enabled = Boolean(data.connection?.enabled);
  const record = data.record;
  const candidate = preview.data;
  return (
    <>
      <div className="ui-two-columns">
        <section className="ui-panel ui-admin-section">
          <h2>{t("deviceUisp.recordTitle")}</h2>
          <p className="ui-muted">{t("deviceGenieacs.recordHelp")}</p>
          <dl className="ui-dl">
            {([["customer", record.customer], ["site", record.site], ["serial", record.serial], ["mac", record.mac], ["acsId", record.external_id]] as const).map(([key, value]) => (
              <div key={key}><dt>{t(`deviceGenieacs.fields.${key}`)}</dt><dd className={key === "customer" || key === "site" ? undefined : "ui-mono"}>{value || "—"}</dd></div>
            ))}
          </dl>
        </section>
        <section className="ui-panel ui-admin-section">
          <h2>{t("deviceGenieacs.connectionTitle")}</h2>
          {data.connection ? (
            <dl className="ui-dl">
              <div><dt>{t("deviceUisp.fields.endpoint")}</dt><dd className="ui-mono">{data.connection.base_url}</dd></div>
              <div><dt>{t("deviceUisp.fields.enabled")}</dt><dd>{data.connection.enabled ? t("deviceUisp.yes") : t("deviceUisp.no")}</dd></div>
              <div><dt>{t("deviceUisp.fields.lastTest")}</dt><dd>{formatDateTime(data.connection.last_tested_at, i18n.language)} · {data.connection.last_test_status ?? t("deviceGenieacs.never")}</dd></div>
            </dl>
          ) : (
            <div className="ui-alert ui-alert-warning">{t("deviceGenieacs.notConfigured")}{" "}
              {data.can.configure && <Link to="/admin/integrations/genieacs">{t("deviceUisp.configure")}</Link>}</div>
          )}
        </section>
      </div>
      {data.inventory && (
        <section className="ui-panel ui-admin-section">
          <div className="ui-page-header-row">
            <div>
              <h2>{t("deviceGenieacs.inventoryTitle")}</h2>
              <p className="ui-muted">{t("deviceGenieacs.inventoryHelp", { when: formatDateTime(data.inventory.last_sync_at, i18n.language),
                by: t(`deviceGenieacs.matchedBy.${data.inventory.matched_by ?? "unknown"}`, { defaultValue: data.inventory.matched_by ?? "—" }) })}</p>
            </div>
            {data.can.write && enabled && (
              <button type="button" className="ui-button" disabled={refresh.isPending} onClick={() => refresh.mutate()}>{t("deviceGenieacs.refresh")}</button>
            )}
          </div>
          {refresh.isSuccess && <div className="ui-alert ui-alert-success">{t("deviceGenieacs.refreshed")}</div>}
          {refresh.error && <div className="ui-alert ui-alert-danger">{message(refresh.error)}</div>}
          <dl className="ui-dl">
            <div><dt>{t("deviceGenieacs.fields.manufacturer")}</dt><dd>{data.inventory.manufacturer || "—"}</dd></div>
            <div><dt>Product class</dt><dd>{data.inventory.product_class || "—"}</dd></div>
            <div><dt>{t("deviceGenieacs.fields.model")}</dt><dd>{record.model || "—"}</dd></div>
            <div><dt>Firmware</dt><dd className="ui-mono">{record.firmware_version || "—"}</dd></div>
            <div><dt>Hardware</dt><dd>{data.inventory.hardware_version || "—"}</dd></div>
            <div><dt>{t("deviceGenieacs.fields.lastInform")}</dt><dd>{formatDateTime(data.inventory.last_inform, i18n.language)}</dd></div>
          </dl>
          <p className="ui-muted">{t("deviceGenieacs.notYet")}</p>
        </section>
      )}
      {record.external_id && <AcsMonitor deviceId={deviceId} />}
      {record.external_id && enabled && <AcsDiagnostics deviceId={deviceId} />}
      {record.external_id && enabled && <AcsSpeedtest deviceId={deviceId} />}
      {record.external_id && enabled && <AcsWifi deviceId={deviceId} deviceName={record.name} />}
      {record.external_id && enabled && <AcsSnapshot deviceId={deviceId} />}
      {record.external_id && <AcsPortMapping deviceId={deviceId} />}
      {record.external_id && enabled && <TplinkAccess deviceId={deviceId} />}
      {record.external_id && enabled && <TplinkSettings deviceId={deviceId} />}
      <section className="ui-panel ui-admin-section">
        <div className="ui-page-header-row">
          <div>
            <h2>{record.external_id ? t("deviceGenieacs.verifyTitle") : t("deviceGenieacs.searchTitle")}</h2>
            <p className="ui-muted">{record.serial
              ? (record.mac ? t("deviceGenieacs.searchSerialMac", { serial: record.serial, mac: record.mac }) : t("deviceGenieacs.searchSerial", { serial: record.serial }))
              : t("deviceGenieacs.searchMac", { mac: record.mac ?? "—" })}</p>
          </div>
          {enabled && <button type="button" className="ui-button ui-button-ghost" disabled={preview.isPending} onClick={() => preview.mutate()}>{t("deviceGenieacs.search")}</button>}
        </div>
        {preview.error && <div className="ui-alert ui-alert-danger">{message(preview.error)}</div>}
        {associate.isSuccess && <div className="ui-alert ui-alert-success">{t("deviceGenieacs.associated")}</div>}
        {associate.error && <div className="ui-alert ui-alert-danger">{message(associate.error)}</div>}
        {candidate && (
          <>
            <h3>{t("deviceGenieacs.previewTitle", { by: t(`deviceGenieacs.matchedBy.${candidate.matched_by ?? "unknown"}`, { defaultValue: candidate.matched_by ?? "—" }) })}</h3>
            <dl className="ui-dl">
              {(["external_id", "serial_number", "primary_mac", "manufacturer", "model", "firmware_version", "management_ip", "hardware_version", "product_class"] as const).map((key) => (
                <div key={key}><dt>{t(`deviceGenieacs.candidate.${key}`)}</dt><dd className="ui-mono">{candidate[key] || "—"}</dd></div>
              ))}
              <div><dt>{t("deviceGenieacs.fields.lastInform")}</dt><dd>{formatDateTime(candidate.last_seen, i18n.language)}</dd></div>
            </dl>
            {data.can.write && (
              <button type="button" className="ui-button" disabled={associate.isPending} onClick={() => associate.mutate()}>
                {record.external_id ? t("deviceGenieacs.confirmRefresh") : t("deviceGenieacs.associate")}
              </button>
            )}
          </>
        )}
      </section>
    </>
  );
}
