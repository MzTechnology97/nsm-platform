import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { formatBytes, formatDate, formatDateTime } from "../lib/format";
import { Loading } from "./States";

interface UispUpgrade {
  state: "requested" | "verified" | "failed" | "unconfirmed";
  version: string;
  from_version: string | null;
  maintenance_window: boolean;
  requested_at: string;
  requested_by: string | null;
  progress: number | null;
  finished_at?: string;
  detail?: string;
}

interface UispFirmwareData {
  can_run: boolean;
  confirm_name: string;
  upgrade: UispUpgrade | null;
  installed: string | null;
  platform: string | null;
  recommended: string | null;
  firmware_status: string | null;
  versions: { version: string; date: number | null; size: number | null; compatibility: string | null }[];
  error: string | null;
}

const path = (deviceId: string) => `/api/ui/v1/devices/${deviceId}/uisp/firmware`;

/** Firmware tab of a Ubiquiti device linked to UISP: installed vs available, upgrade queued in UISP and verified. */
export function UispFirmware({ deviceId }: { deviceId: string }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const key = ["uisp-firmware", deviceId];
  const query = useQuery({
    queryKey: key,
    queryFn: () => api<UispFirmwareData>(path(deviceId)),
    refetchInterval: (state) => (state.state.data?.upgrade?.state === "requested" ? 20000 : false),
  });
  const [version, setVersion] = useState("");
  const [windowed, setWindowed] = useState(false);
  const [confirm, setConfirm] = useState("");
  const data = query.data;
  useEffect(() => {
    if (!data || version) return;
    const preferred = data.versions.find((v) => v.version === data.recommended) ?? data.versions[0];
    if (preferred) setVersion(preferred.version);
  }, [data, version]);
  const run = useMutation({
    mutationFn: () => api<UispFirmwareData>(path(deviceId), { method: "POST", body: JSON.stringify({ version, confirm, maintenance_window: windowed }) }),
    onSuccess: (result) => {
      setConfirm("");
      client.setQueryData(key, result);
      void client.invalidateQueries({ queryKey: ["firmware"] });
    },
  });
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <Loading />;
  const upgrade = data.upgrade;
  const busy = upgrade?.state === "requested";
  const submit = (event: FormEvent) => {
    event.preventDefault();
    run.mutate();
  };

  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("uispFirmware.title")}</h2>
      <p className="ui-muted">{t("uispFirmware.help")}</p>
      <dl className="ui-kv">
        <dt>{t("uispFirmware.installed")}</dt><dd className="ui-mono">{data.installed ?? "—"}{data.platform && <small className="ui-muted"> · {t("uispFirmware.platform", { platform: data.platform })}</small>}</dd>
        <dt>{t("uispFirmware.recommended")}</dt>
        <dd className="ui-mono">{data.recommended ?? "—"} <small className="ui-muted">{t(`firmware.status.${data.firmware_status ?? "unknown"}`, { defaultValue: data.firmware_status ?? "" })}</small></dd>
      </dl>
      {data.error && <div className="ui-alert ui-alert-warning">{data.error}</div>}
      {upgrade && (
        <div className={`ui-alert ${upgrade.state === "verified" ? "ui-alert-success" : upgrade.state === "requested" ? "ui-alert-info" : "ui-alert-warning"}`}>
          <strong>{t(`uispFirmware.states.${upgrade.state}`, { version: upgrade.version })}</strong>{" "}
          {upgrade.state === "requested" && upgrade.progress != null && t("uispFirmware.progress", { percent: Math.round(upgrade.progress * 100) })}{" "}
          {upgrade.state === "requested" && upgrade.maintenance_window && t("uispFirmware.inWindow")}
          <small className="ui-block">
            {t("uispFirmware.requested", { when: formatDateTime(upgrade.requested_at, i18n.language), who: upgrade.requested_by ?? "—", from: upgrade.from_version ?? "—" })}
            {upgrade.detail && <> · {upgrade.detail}</>}
          </small>
        </div>
      )}
      {data.can_run && !busy && (
        data.versions.length ? (
          <form className="ui-admin-form" onSubmit={submit}>
            <label>
              <span>{t("uispFirmware.version")}</span>
              <select value={version} onChange={(event) => setVersion(event.target.value)}>
                {data.versions.map((v) => (
                  <option key={v.version} value={v.version}>
                    {v.version}{v.date ? ` · ${formatDate(new Date(v.date).toISOString(), i18n.language)}` : ""}{v.size ? ` · ${formatBytes(v.size, i18n.language)}` : ""}
                    {v.version === data.recommended ? ` · ${t("uispFirmware.recommendedTag")}` : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="ui-with-brand ui-admin-wide">
              <input type="checkbox" checked={windowed} onChange={(event) => setWindowed(event.target.checked)} /> <span>{t("uispFirmware.window")}</span>
            </label>
            <label className="ui-admin-wide">
              <span>{t("uispFirmware.confirm", { name: data.confirm_name })}</span>
              <input value={confirm} onChange={(event) => setConfirm(event.target.value)} autoComplete="off" />
            </label>
            <div className="ui-actions ui-admin-wide">
              <button type="submit" className="ui-button ui-button-danger" disabled={run.isPending || !version || confirm.trim() !== data.confirm_name}>
                {run.isPending ? t("app.loading") : t("uispFirmware.start", { version })}
              </button>
            </div>
          </form>
        ) : (
          !data.error && <div className="ui-empty">{t("uispFirmware.noNewer")}</div>
        )
      )}
      {run.error && <div className="ui-alert ui-alert-danger">{(run.error as Error).message}</div>}
    </section>
  );
}
