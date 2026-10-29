import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../api/client";
import { formatDateTime } from "../lib/format";

type Method = "agent" | "tr069" | "uisp" | null;
interface Action { status: string; method: Method; done_at: string | null; requested_at: string | null; by: string | null; error: string | null; applied?: boolean }
interface UpnpState { method: Method; enabled: boolean | null; read_at: string | null; detail: string | null; by_nsm: boolean; action: Action | null; blocker: string | null }
interface DeviceUpnp extends UpnpState { name: string; can_disable: boolean }
interface FleetRow extends UpnpState { id: string; name: string; vendor: string; customer: string | null }
interface Fleet { items: FleetRow[]; counts: { on: number; off: number; unknown: number; in_progress: number; failed: number }; can_disable: boolean; max_bulk: number }

function message(error: unknown) {
  return error instanceof ApiError ? error.message : (error as Error | null)?.message;
}

function Status({ row }: { row: UpnpState }) {
  const { t } = useTranslation();
  const status = row.action?.status;
  if (status === "queued" || status === "running") return <span className="ui-severity">{t(`upnp.status.${status}`)}</span>;
  if (status === "failed" || status === "stopped") return <span className="ui-severity ui-severity-warning" title={row.action?.error ?? ""}>{t(`upnp.status.${status}`)}</span>;
  if (row.enabled === true) return <span className="ui-severity ui-severity-warning">{t("upnp.on")}</span>;
  if (row.enabled === false) return <span className="ui-severity">{row.by_nsm ? t("upnp.offByNsm") : t("upnp.off")}</span>;
  return <span className="ui-muted">{t("upnp.unknown")}</span>;
}

/** UPnP of one device (Exposure tab): state as NSM last read it and the turn-off (airOS reboots: the name is typed). */
export function UpnpDevicePanel({ id }: { id: string }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["device", id, "upnp"], queryFn: () => api<DeviceUpnp>(`/api/ui/v1/devices/${id}/upnp`), retry: false });
  const [typed, setTyped] = useState("");
  const disable = useMutation({
    mutationFn: () => api<DeviceUpnp>(`/api/ui/v1/devices/${id}/upnp/disable`, { method: "POST", body: JSON.stringify({ confirmation: typed }) }),
    onSuccess: () => { setTyped(""); void client.invalidateQueries({ queryKey: ["device", id, "upnp"] }); },
  });
  const data = query.data;
  if (!data || !data.method) return null;
  const busy = data.action?.status === "queued" || data.action?.status === "running";
  const reboot = data.method === "uisp";
  return (
    <section className="ui-panel ui-admin-section">
      <div className="ui-page-header-row">
        <div>
          <h2>UPnP</h2>
          <p className="ui-muted">{t("upnp.hint")}</p>
        </div>
        <Status row={data} />
      </div>
      <p className="ui-muted">
        {t(`upnp.source.${data.method}`)}
        {data.read_at ? ` · ${t("upnp.readAt", { when: formatDateTime(data.read_at, i18n.language) })}` : ""}
        {data.method === "uisp" && data.detail ? ` · ${t("upnp.devname", { name: data.detail })}` : ""}
      </p>
      {data.action?.status === "done" && data.action.done_at && (
        <p className="ui-live">{data.action.applied === false ? t("upnp.waiting") : t("upnp.done", { when: formatDateTime(data.action.done_at, i18n.language), by: data.action.by ?? "NSM" })}</p>
      )}
      {data.action?.error && <div className="ui-alert ui-alert-warning">{data.action.error}</div>}
      {data.enabled === true && data.blocker && <div className="ui-alert ui-alert-warning">{data.blocker}</div>}
      {data.enabled === true && !data.blocker && data.can_disable && !busy && (
        <form className="ui-admin-form ui-admin-stack" onSubmit={(event) => { event.preventDefault(); disable.mutate(); }}>
          {reboot && (
            <>
              <div className="ui-alert ui-alert-warning">{t("upnp.reboot")}</div>
              <label>
                <span>{t("upnp.confirm", { name: data.name })}</span>
                <input value={typed} onChange={(event) => setTyped(event.target.value)} autoComplete="off" />
              </label>
            </>
          )}
          <button type="submit" className="ui-button" disabled={disable.isPending || (reboot && typed.trim() !== data.name)}>
            {disable.isPending ? t("upnp.working") : t("upnp.disable")}
          </button>
          {disable.error && <small className="ui-backup-error">{message(disable.error)}</small>}
        </form>
      )}
    </section>
  );
}

/** Exposure page: devices with UPnP on, every vendor, and the bulk turn-off (the number of devices typed to confirm). */
export function UpnpFleetPanel({ customer }: { customer?: string }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["security", "upnp", customer ?? ""], queryFn: () => api<Fleet>(`/api/ui/v1/security/upnp${customer ? `?customer=${customer}` : ""}`) });
  const [chosen, setChosen] = useState<string[]>([]);
  const [typed, setTyped] = useState("");
  const bulk = useMutation({
    mutationFn: () => api<{ queued: number; skipped: { id: string; name: string; reason: string }[] }>("/api/ui/v1/security/upnp/disable", {
      method: "POST", body: JSON.stringify({ device_ids: chosen, confirmation: typed }),
    }),
    onSuccess: () => { setChosen([]); setTyped(""); void client.invalidateQueries({ queryKey: ["security", "upnp"] }); },
  });
  const data = query.data;
  if (!data) return null;
  const selectable = data.items.filter((row) => row.enabled === true && !row.blocker && row.action?.status !== "queued" && row.action?.status !== "running");
  const toggle = (rowId: string) => setChosen(chosen.includes(rowId) ? chosen.filter((value) => value !== rowId) : [...chosen, rowId]);
  const airos = data.items.filter((row) => chosen.includes(row.id) && row.method === "uisp").length;
  return (
    <section className="ui-panel ui-admin-section">
      <h2>UPnP</h2>
      <p className="ui-muted">{t("upnp.fleetHint")}</p>
      <p>{t("upnp.counts", data.counts)}</p>
      {data.items.length === 0 ? (
        <div className="ui-empty">{t("upnp.none")}</div>
      ) : (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead>
              <tr>
                <th>{data.can_disable && selectable.length > 0 && (
                  <input type="checkbox" aria-label={t("upnp.all")} checked={chosen.length === Math.min(selectable.length, data.max_bulk)}
                    onChange={(event) => setChosen(event.target.checked ? selectable.slice(0, data.max_bulk).map((row) => row.id) : [])} />
                )}</th>
                {["device", "customer", "method", "state"].map((key) => <th key={key}><span className="ui-th">{t(`upnp.columns.${key}`)}</span></th>)}
              </tr>
            </thead>
            <tbody>
              {data.items.map((row) => (
                <tr key={row.id}>
                  <td>{data.can_disable && selectable.includes(row) && <input type="checkbox" checked={chosen.includes(row.id)} onChange={() => toggle(row.id)} aria-label={row.name} />}</td>
                  <td><Link to="/devices/$deviceId/exposure" params={{ deviceId: row.id }}>{row.name}</Link></td>
                  <td>{row.customer ?? "—"}</td>
                  <td>{t(`upnp.method.${row.method}`)}</td>
                  <td><Status row={row} />{row.enabled === true && row.blocker && <small className="ui-muted"> {row.blocker}</small>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data.can_disable && chosen.length > 0 && (
        <form className="ui-admin-form ui-admin-stack" onSubmit={(event) => { event.preventDefault(); bulk.mutate(); }}>
          {airos > 0 && <div className="ui-alert ui-alert-warning">{t("upnp.bulkReboot", { count: airos })}</div>}
          <label>
            <span>{t("upnp.bulkConfirm", { count: chosen.length })}</span>
            <input value={typed} onChange={(event) => setTyped(event.target.value)} autoComplete="off" inputMode="numeric" />
          </label>
          <button type="submit" className="ui-button" disabled={bulk.isPending || typed.trim() !== String(chosen.length)}>{t("upnp.bulkDisable", { count: chosen.length })}</button>
          {bulk.error && <small className="ui-backup-error">{message(bulk.error)}</small>}
        </form>
      )}
      {bulk.data && <p className="ui-live">{t("upnp.bulkQueued", { count: bulk.data.queued })}{bulk.data.skipped.length > 0 ? ` ${t("upnp.bulkSkipped", { count: bulk.data.skipped.length })}` : ""}</p>}
    </section>
  );
}
