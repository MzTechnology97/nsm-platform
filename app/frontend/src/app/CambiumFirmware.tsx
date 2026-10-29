import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../api/client";
import { formatBytes, formatDate, formatDateTime } from "../lib/format";
import { Loading } from "./States";

interface CambiumUpgrade {
  state: "requested" | "verified" | "failed" | "unconfirmed";
  version: string;
  from_version: string | null;
  file?: string;
  requested_at: string;
  requested_by: string | null;
  agent_label?: string | null;
  seen_running?: boolean;
  image_served_at?: string | null;
  finished_at?: string;
  detail?: string;
}

interface CambiumImage { release_id: string; version: string; released_at: string | null; file: string; size: number | null; security: boolean; newer: boolean }

interface CambiumFirmwareData {
  can_run: boolean;
  confirm_name: string;
  installed: string | null;
  recommended: string | null;
  firmware_status: string | null;
  credentials: "device" | "shared" | "none";
  management_ip: string | null;
  linked_cnmaestro: boolean;
  server: string | null;
  images: CambiumImage[];
  upgrade: CambiumUpgrade | null;
}

interface CheckResult {
  ready: boolean;
  community_rw: boolean;
  agent: { status: number | null; status_label: string | null; error: number | null; error_text: string | null };
  software: string | null;
  checked_at: string;
}

const path = (deviceId: string) => `/api/ui/v1/devices/${deviceId}/cambium/firmware`;

function message(error: unknown) {
  return error instanceof ApiError ? error.message : (error as Error | null)?.message;
}

/** Firmware tab of a Cambium ePMP: image from the NSM catalog, SSH backup, update link set on the radio, verified afterwards. */
export function CambiumFirmware({ deviceId }: { deviceId: string }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const key = ["cambium-firmware", deviceId];
  const query = useQuery({
    queryKey: key,
    queryFn: () => api<CambiumFirmwareData>(path(deviceId)),
    refetchInterval: (state) => (state.state.data?.upgrade?.state === "requested" ? 20000 : false),
  });
  const [release, setRelease] = useState("");
  const [confirm, setConfirm] = useState("");
  const data = query.data;
  const newer = data?.images.filter((image) => image.newer) ?? [];
  const first = newer[0]?.release_id;
  useEffect(() => {
    if (!release && first) setRelease(first);
  }, [first, release]);
  const check = useMutation({ mutationFn: () => api<CheckResult>(`${path(deviceId)}/check`, { method: "POST" }) });
  const run = useMutation({
    mutationFn: () => api<CambiumFirmwareData>(path(deviceId), { method: "POST", body: JSON.stringify({ release_id: release, confirm }) }),
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
  const chosen = newer.find((image) => image.release_id === release);
  const ready = data.credentials !== "none" && !!data.management_ip && !!data.server;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    run.mutate();
  };
  const result = check.data;

  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("cambiumFirmware.title")}</h2>
      <p className="ui-muted">{t("cambiumFirmware.help")}</p>
      <dl className="ui-kv">
        <dt>{t("cambiumFirmware.installed")}</dt><dd className="ui-mono">{data.installed ?? "—"}</dd>
        <dt>{t("cambiumFirmware.recommended")}</dt>
        <dd className="ui-mono">{data.recommended ?? "—"} <small className="ui-muted">{t(`firmware.status.${data.firmware_status ?? "unknown"}`, { defaultValue: data.firmware_status ?? "" })}</small></dd>
        <dt>{t("cambiumFirmware.server")}</dt><dd className="ui-mono">{data.server ?? "—"}</dd>
        <dt>{t("cambiumFirmware.verifiedBy")}</dt><dd>{data.linked_cnmaestro ? t("cambiumFirmware.byCnmaestro") : t("cambiumFirmware.bySsh")}</dd>
      </dl>
      {data.credentials === "none" && <div className="ui-alert ui-alert-warning">{t("cambiumFirmware.noCredentials")}</div>}
      {!data.server && <div className="ui-alert ui-alert-warning">{t("cambiumFirmware.noServer")}</div>}
      {upgrade && (
        <div className={`ui-alert ${upgrade.state === "verified" ? "ui-alert-success" : upgrade.state === "requested" ? "ui-alert-info" : "ui-alert-warning"}`}>
          <strong>{t(`cambiumFirmware.states.${upgrade.state}`, { version: upgrade.version })}</strong>{" "}
          {upgrade.state === "requested" && upgrade.agent_label && t("cambiumFirmware.agent", { state: t(`cambiumFirmware.agentStates.${upgrade.agent_label}`, { defaultValue: upgrade.agent_label }) })}{" "}
          {upgrade.state === "requested" && (upgrade.image_served_at ? t("cambiumFirmware.served") : t("cambiumFirmware.notServed"))}
          <small className="ui-block">
            {t("cambiumFirmware.requested", { when: formatDateTime(upgrade.requested_at, i18n.language), who: upgrade.requested_by ?? "—", from: upgrade.from_version ?? "—" })}
            {upgrade.detail && <> · {upgrade.detail}</>}
          </small>
        </div>
      )}
      {data.can_run && ready && (
        <div className="ui-row-actions">
          <button type="button" className="ui-button ui-button-ghost" disabled={check.isPending} onClick={() => check.mutate()}>
            {check.isPending ? t("app.loading") : t("cambiumFirmware.check")}
          </button>
          {result && (
            <span className={result.ready ? "ui-live" : "ui-backup-error"}>
              {result.ready ? t("cambiumFirmware.checkReady") : t("cambiumFirmware.checkBusy")}{" "}
              {t("cambiumFirmware.checkAgent", { state: t(`cambiumFirmware.agentStates.${result.agent.status_label ?? "unknown"}`, { defaultValue: result.agent.status_label ?? "?" }) })}
              {result.agent.error ? ` · ${t("cambiumFirmware.lastError", { error: result.agent.error_text ?? result.agent.error })}` : ""}
              {result.software ? ` · ${result.software}` : ""}
            </span>
          )}
          {check.error && <small className="ui-backup-error">{message(check.error)}</small>}
        </div>
      )}
      {data.can_run && !busy && ready && (
        newer.length ? (
          <form className="ui-admin-form" onSubmit={submit}>
            <label>
              <span>{t("cambiumFirmware.image")}</span>
              <select value={release} onChange={(event) => setRelease(event.target.value)}>
                {newer.map((image) => (
                  <option key={image.release_id} value={image.release_id}>
                    {image.version} · {image.file}{image.released_at ? ` · ${formatDate(image.released_at, i18n.language)}` : ""}
                    {image.size ? ` · ${formatBytes(image.size, i18n.language)}` : ""}{image.security ? ` · ${t("cambiumFirmware.security")}` : ""}
                  </option>
                ))}
              </select>
            </label>
            <ol className="ui-admin-wide ui-muted">
              <li>{t("cambiumFirmware.steps.backup")}</li>
              <li>{t("cambiumFirmware.steps.command", { server: data.server })}</li>
              <li>{t("cambiumFirmware.steps.reboot")}</li>
              <li>{t("cambiumFirmware.steps.verify")}</li>
            </ol>
            <label className="ui-admin-wide">
              <span>{t("cambiumFirmware.confirm", { name: data.confirm_name })}</span>
              <input value={confirm} onChange={(event) => setConfirm(event.target.value)} autoComplete="off" />
            </label>
            <div className="ui-actions ui-admin-wide">
              <button type="submit" className="ui-button ui-button-danger" disabled={run.isPending || !chosen || confirm.trim() !== data.confirm_name}>
                {run.isPending ? t("cambiumFirmware.running") : t("cambiumFirmware.start", { version: chosen?.version ?? "" })}
              </button>
            </div>
          </form>
        ) : (
          <div className="ui-empty">{data.images.length ? t("cambiumFirmware.noNewer") : t("cambiumFirmware.noImages")}</div>
        )
      )}
      {run.error && <div className="ui-alert ui-alert-danger">{message(run.error)}</div>}
    </section>
  );
}
