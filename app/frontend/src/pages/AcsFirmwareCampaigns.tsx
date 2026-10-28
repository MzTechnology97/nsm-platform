import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { type FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../api/client";
import { formatBytes, formatDateTime } from "../lib/format";
import { Loading } from "../app/States";
import { BrandIcon, type BrandRef } from "../app/BrandIcon";

interface CampaignSummary {
  id: string;
  name: string;
  status: "draft" | "running" | "paused" | "stopped" | "completed" | "cancelled";
  product_class: string | null;
  file_name: string;
  expected_version: string | null;
  wave_size: number;
  window_start: string;
  window_end: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  stop_reason: string | null;
  counts: Record<string, number>;
  total: number;
  timezone: string;
}

interface CampaignDevice {
  device_id: string;
  name: string | null;
  customer: string | null;
  brand: BrandRef | null;
  wave: number;
  status: string;
  from_version: string | null;
  to_version: string | null;
  requested_at: string | null;
  completed_at: string | null;
  error: string | null;
}

interface CampaignDetail extends CampaignSummary {
  devices: CampaignDevice[];
  can_manage: boolean;
  in_window: boolean;
}

interface Options {
  files: { name: string; size: number | null; product_class: string | null; version: string | null; uploaded_at: string | null }[];
  devices: { id: string; name: string; brand: BrandRef | null; customer: string | null; firmware_version: string | null; status: string }[];
  default_wave_size: number;
  max_wave_size: number;
  timezone: string;
}

const BASE = "/api/ui/v1/acs/firmware-campaigns";
const STATUS_CLASS: Record<string, string> = {
  running: "ui-status ui-status-online", paused: "ui-status ui-status-warning", stopped: "ui-status ui-status-danger",
  completed: "ui-status ui-status-success", cancelled: "ui-status ui-status-offline", draft: "ui-status ui-status-pending",
  verified: "ui-status ui-status-success", requested: "ui-status ui-status-online", pending: "ui-status ui-status-pending",
  skipped: "ui-status ui-status-offline", failed: "ui-status ui-status-danger", unconfirmed: "ui-status ui-status-danger",
};

function message(error: unknown) {
  return error instanceof ApiError ? error.message : (error as Error | null)?.message;
}

function Counts({ campaign }: { campaign: CampaignSummary }) {
  const { t } = useTranslation();
  const order = ["verified", "requested", "pending", "skipped", "failed", "unconfirmed", "cancelled"];
  return (
    <span className="ui-muted">
      {order.filter((key) => campaign.counts[key]).map((key) => `${t(`acsCampaigns.device.${key}`)} ${campaign.counts[key]}`).join(" · ") || "—"}
    </span>
  );
}

/** Firmware campaigns over many TR-069 CPEs: list and creation of a draft (ACS-08). */
export function AcsFirmwareCampaigns() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const list = useQuery({ queryKey: ["acs-campaigns"], queryFn: () => api<{ items: CampaignSummary[]; can_manage: boolean }>(BASE) });
  const [creating, setCreating] = useState(false);
  const [productClass, setProductClass] = useState("");
  const options = useQuery({
    queryKey: ["acs-campaign-options", productClass],
    queryFn: () => api<Options>(`${BASE}/options?product_class=${encodeURIComponent(productClass)}`),
    enabled: creating,
    retry: false,
  });
  const [form, setForm] = useState({ name: "", file_name: "", expected_version: "", wave_size: 10, window_start: "01:00", window_end: "05:00", pilot: "" });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  useEffect(() => {
    setSelected(new Set((options.data?.devices ?? []).map((device) => device.id)));
  }, [options.data?.devices]);
  const create = useMutation({
    mutationFn: () => api<CampaignDetail>(BASE, {
      method: "POST",
      body: JSON.stringify({ ...form, pilot_device_id: form.pilot || undefined, expected_version: form.expected_version || undefined, device_ids: [...selected] }),
    }),
    onSuccess: (data) => navigate({ to: "/firmware/campaigns/$campaignId", params: { campaignId: data.id } }),
  });
  if (list.error) return <div className="ui-alert ui-alert-danger">{message(list.error)}</div>;
  if (!list.data) return <Loading />;
  const files = options.data?.files ?? [];
  const chooseFile = (name: string) => {
    const file = files.find((item) => item.name === name);
    setForm({ ...form, file_name: name, expected_version: file?.version ?? "", pilot: "" });
    setProductClass(file?.product_class ?? "");
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate();
  };
  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("acsCampaigns.title")}</h1>
          <p>{t("acsCampaigns.subtitle")}</p>
        </div>
        <div className="ui-actions">
          <Link to="/firmware" className="ui-button ui-button-ghost">{t("acsCampaigns.back")}</Link>
          {list.data.can_manage && !creating && <button type="button" className="ui-button" onClick={() => setCreating(true)}>{t("acsCampaigns.new")}</button>}
        </div>
      </header>

      {creating && (
        <section className="ui-panel ui-admin-section">
          <h2>{t("acsCampaigns.newTitle")}</h2>
          <p className="ui-muted">{t("acsCampaigns.newHelp")}</p>
          {options.error && <div className="ui-alert ui-alert-danger">{message(options.error)}</div>}
          <form className="ui-admin-form" onSubmit={submit}>
            <label>
              <span>{t("acsCampaigns.fields.name")}</span>
              <input value={form.name} maxLength={120} onChange={(event) => setForm({ ...form, name: event.target.value })} required />
            </label>
            <label>
              <span>{t("acsCampaigns.fields.file")}</span>
              <select value={form.file_name} onChange={(event) => chooseFile(event.target.value)} required>
                <option value="">{t("acsCampaigns.chooseFile")}</option>
                {files.map((file) => (
                  <option key={file.name} value={file.name}>
                    {file.product_class} · {file.version ?? "—"} · {file.name}{file.size ? ` · ${formatBytes(file.size, i18n.language)}` : ""}
                  </option>
                ))}
              </select>
            </label>
            {options.data && !files.length && <p className="ui-muted">{t("acsCampaigns.noFiles")}</p>}
            <label>
              <span>{t("acsCampaigns.fields.expected")}</span>
              <input value={form.expected_version} maxLength={120} onChange={(event) => setForm({ ...form, expected_version: event.target.value })} />
            </label>
            <label>
              <span>{t("acsCampaigns.fields.waveSize")}</span>
              <input type="number" min={1} max={options.data?.max_wave_size ?? 50} value={form.wave_size}
                onChange={(event) => setForm({ ...form, wave_size: Number(event.target.value) })} />
            </label>
            <label>
              <span>{t("acsCampaigns.fields.window", { tz: options.data?.timezone ?? "" })}</span>
              <span className="ui-row-actions">
                <input type="time" value={form.window_start} onChange={(event) => setForm({ ...form, window_start: event.target.value })} required />
                <input type="time" value={form.window_end} onChange={(event) => setForm({ ...form, window_end: event.target.value })} required />
              </span>
            </label>
            {productClass && options.data && (
              <>
                <label>
                  <span>{t("acsCampaigns.fields.pilot")}</span>
                  <select value={form.pilot} onChange={(event) => setForm({ ...form, pilot: event.target.value })}>
                    <option value="">{t("acsCampaigns.pilotAuto")}</option>
                    {options.data.devices.filter((device) => selected.has(device.id)).map((device) => (
                      <option key={device.id} value={device.id}>{device.name}{device.customer ? ` · ${device.customer}` : ""}</option>
                    ))}
                  </select>
                </label>
                <p className="ui-muted">{t("acsCampaigns.selected", { count: selected.size, total: options.data.devices.length, model: productClass })}</p>
                <div className="ui-table-scroll">
                  <table className="ui-stats-table">
                    <thead>
                      <tr><th /><th>{t("acsCampaigns.columns.device")}</th><th>{t("acsCampaigns.columns.customer")}</th><th>{t("acsCampaigns.columns.version")}</th><th>{t("acsCampaigns.columns.state")}</th></tr>
                    </thead>
                    <tbody>
                      {options.data.devices.map((device) => (
                        <tr key={device.id}>
                          <td><input type="checkbox" aria-label={device.name} checked={selected.has(device.id)} onChange={() => toggle(device.id)} /></td>
                          <td className="ui-with-brand"><BrandIcon brand={device.brand} small /> <Link to="/devices/$deviceId" params={{ deviceId: device.id }}>{device.name}</Link></td>
                          <td>{device.customer ?? "—"}</td>
                          <td className="ui-mono">{device.firmware_version ?? "—"}</td>
                          <td>{device.status}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
            <div className="ui-row-actions">
              <button type="submit" className="ui-button" disabled={create.isPending || !form.file_name || !selected.size}>{t("acsCampaigns.createDraft")}</button>
              <button type="button" className="ui-button ui-button-ghost" onClick={() => setCreating(false)}>{t("acsCampaigns.close")}</button>
            </div>
          </form>
          {create.error && <div className="ui-alert ui-alert-danger">{message(create.error)}</div>}
        </section>
      )}

      <section className="ui-panel">
        {list.data.items.length === 0 ? (
          <div className="ui-empty">{t("acsCampaigns.empty")}</div>
        ) : (
          <div className="ui-table-scroll">
            <table className="ui-stats-table">
              <thead>
                <tr><th>{t("acsCampaigns.columns.name")}</th><th>{t("acsCampaigns.columns.state")}</th><th>{t("acsCampaigns.columns.model")}</th>
                  <th>{t("acsCampaigns.columns.progress")}</th><th>{t("acsCampaigns.columns.created")}</th></tr>
              </thead>
              <tbody>
                {list.data.items.map((campaign) => (
                  <tr key={campaign.id}>
                    <td><Link to="/firmware/campaigns/$campaignId" params={{ campaignId: campaign.id }}>{campaign.name}</Link></td>
                    <td><span className={STATUS_CLASS[campaign.status]}>{t(`acsCampaigns.status.${campaign.status}`)}</span></td>
                    <td>{campaign.product_class ?? "—"} → <span className="ui-mono">{campaign.expected_version ?? "—"}</span></td>
                    <td><Counts campaign={campaign} /></td>
                    <td>{formatDateTime(campaign.created_at, i18n.language)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </section>
  );
}

/** One campaign: state of every CPE by wave; start/resume with the typed name, pause, cancel. */
export function AcsFirmwareCampaign() {
  const { t, i18n } = useTranslation();
  const { campaignId } = useParams({ from: "/firmware/campaigns/$campaignId" });
  const query = useQuery({
    queryKey: ["acs-campaign", campaignId],
    queryFn: () => api<CampaignDetail>(`${BASE}/${encodeURIComponent(campaignId)}`),
    refetchInterval: (state) => (["running", "paused"].includes(state.state.data?.status ?? "") ? 15000 : false),
  });
  const [confirm, setConfirm] = useState("");
  const act = useMutation({
    mutationFn: (action: "start" | "pause" | "cancel") =>
      api<CampaignDetail>(`${BASE}/${encodeURIComponent(campaignId)}/${action}`, { method: "POST", body: JSON.stringify({ confirm }) }),
    onSuccess: (data) => {
      setConfirm("");
      query.refetch();
      return data;
    },
  });
  if (query.error) return <div className="ui-alert ui-alert-danger">{message(query.error)}</div>;
  const data = act.data && query.data && act.data.status !== query.data.status ? act.data : query.data;
  if (!data) return <Loading />;
  const startable = ["draft", "paused", "stopped"].includes(data.status);
  const waves = [...new Set(data.devices.map((device) => device.wave))].sort((a, b) => a - b);
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{data.name}</h1>
          <p>
            <span className={STATUS_CLASS[data.status]}>{t(`acsCampaigns.status.${data.status}`)}</span>{" "}
            {t("acsCampaigns.summary", { model: data.product_class ?? "—", version: data.expected_version ?? "—", file: data.file_name, wave: data.wave_size,
              start: data.window_start, end: data.window_end, tz: data.timezone })}
          </p>
        </div>
        <div className="ui-actions">
          <Link to="/firmware/campaigns" className="ui-button ui-button-ghost">{t("acsCampaigns.backList")}</Link>
        </div>
      </header>
      {data.stop_reason && <div className="ui-alert ui-alert-warning">{t("acsCampaigns.stopped", { reason: data.stop_reason })}</div>}
      {data.status === "running" && !data.in_window && <div className="ui-alert">{t("acsCampaigns.outsideWindow")}</div>}
      <section className="ui-panel ui-admin-section">
        <p className="ui-muted">{t("acsCampaigns.rules")}</p>
        <p><Counts campaign={data} /></p>
        {data.can_manage && !["completed", "cancelled"].includes(data.status) && (
          <form className="ui-admin-form" onSubmit={(event) => { event.preventDefault(); act.mutate("start"); }}>
            {startable && (
              <label>
                <span>{t(data.status === "draft" ? "acsCampaigns.confirmStart" : "acsCampaigns.confirmResume", { name: data.name })}</span>
                <input value={confirm} onChange={(event) => setConfirm(event.target.value)} autoComplete="off" />
              </label>
            )}
            <div className="ui-row-actions">
              {startable && (
                <button type="submit" className="ui-button ui-button-danger" disabled={act.isPending || confirm.trim() !== data.name}>
                  {t(data.status === "draft" ? "acsCampaigns.start" : "acsCampaigns.resume")}
                </button>
              )}
              {data.status === "running" && (
                <button type="button" className="ui-button ui-button-ghost" disabled={act.isPending} onClick={() => act.mutate("pause")}>{t("acsCampaigns.pause")}</button>
              )}
              <button type="button" className="ui-button ui-button-ghost" disabled={act.isPending} onClick={() => act.mutate("cancel")}>{t("acsCampaigns.cancel")}</button>
            </div>
          </form>
        )}
        {act.error && <div className="ui-alert ui-alert-danger">{message(act.error)}</div>}
      </section>
      {waves.map((wave) => (
        <section key={wave} className="ui-panel ui-admin-section">
          <h2>{wave === 0 ? t("acsCampaigns.pilot") : t("acsCampaigns.wave", { number: wave })}</h2>
          <div className="ui-table-scroll">
            <table className="ui-stats-table">
              <thead>
                <tr><th>{t("acsCampaigns.columns.device")}</th><th>{t("acsCampaigns.columns.customer")}</th><th>{t("acsCampaigns.columns.state")}</th>
                  <th>{t("acsCampaigns.columns.version")}</th><th>{t("acsCampaigns.columns.when")}</th><th>{t("acsCampaigns.columns.error")}</th></tr>
              </thead>
              <tbody>
                {data.devices.filter((device) => device.wave === wave).map((device) => (
                  <tr key={device.device_id}>
                    <td className="ui-with-brand">
                      <BrandIcon brand={device.brand} small />{" "}
                      <Link to="/devices/$deviceId" params={{ deviceId: device.device_id }}>{device.name ?? device.device_id}</Link>
                    </td>
                    <td>{device.customer ?? "—"}</td>
                    <td><span className={STATUS_CLASS[device.status] ?? "ui-status"}>{t(`acsCampaigns.device.${device.status}`)}</span></td>
                    <td className="ui-mono">{device.from_version ?? "—"}{device.to_version ? ` → ${device.to_version}` : ""}</td>
                    <td>{formatDateTime(device.completed_at ?? device.requested_at, i18n.language)}</td>
                    <td>{device.error ? t(`acsCampaigns.errors.${device.error}`, { defaultValue: device.error }) : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </section>
  );
}
