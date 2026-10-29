import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { formatDateTime } from "../lib/format";
import { TplinkCloudFirmware } from "./TplinkCloudFirmware";

interface FirmwareFile { name: string; size: number | null; uploaded_at: string | null; product_class: string | null; version: string | null }

interface Upgrade {
  state: "requested" | "verified" | "failed" | "unconfirmed";
  file_name: string;
  expected_version: string | null;
  from_version: string | null;
  to_version: string | null;
  matches?: boolean | null;
  requested_at: string;
  completed_at: string | null;
  by: string | null;
  started: boolean;
  fault: { code: string; message: string; at: string } | null;
  task_removed?: boolean;
}

interface AcsFirmwareData {
  linked: boolean;
  can_run: boolean;
  confirm_name: string;
  current_version: string | null;
  product_class: string | null;
  files: FirmwareFile[];
  files_error: string | null;
  max_mb: number;
  upgrade: Upgrade | null;
  /** Newest release of the firmware catalog for this model (Firmware → Cataloghi), as for the other devices. */
  catalog: { latest: string; source: string; security: boolean; status: string | null; newer: boolean; fetchable: boolean; released_at: string | null; notes_url: string | null } | null;
}

const path = (id: string) => `/api/ui/v1/devices/${encodeURIComponent(id)}/acs/firmware`;
const mb = (bytes: number | null) => (bytes == null ? "—" : `${(bytes / 1048576).toFixed(1)} MB`);

/** Firmware upgrade of a TR-069 CPE through GenieACS (ACS-08): image stored in GenieACS, download task, verified from the reported version. */
export function AcsFirmware({ deviceId }: { deviceId: string }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const key = ["device", deviceId, "acs-firmware"];
  const query = useQuery({
    queryKey: key,
    queryFn: () => api<AcsFirmwareData>(path(deviceId)),
    refetchInterval: (state) => (state.state.data?.upgrade?.state === "requested" ? 15000 : false),
  });
  const [image, setImage] = useState<File | null>(null);
  const [version, setVersion] = useState("");
  const [chosen, setChosen] = useState("");
  const [confirm, setConfirm] = useState("");
  const upload = useMutation({
    mutationFn: () => {
      const form = new FormData();
      if (image) form.append("image", image);
      form.append("version", version);
      return api<{ file_name: string; size: number }>(`${path(deviceId)}/files`, { method: "POST", body: form });
    },
    onSuccess: (data) => {
      setChosen(data.file_name);
      setImage(null);
      void client.invalidateQueries({ queryKey: key });
    },
  });
  const fetchImage = useMutation({
    mutationFn: (target: string) => api<{ file_name: string; size: number }>(`${path(deviceId)}/fetch`, { method: "POST", body: JSON.stringify({ version: target }) }),
    onSuccess: (result) => {
      setChosen(result.file_name);
      void client.invalidateQueries({ queryKey: key });
    },
  });
  const run = useMutation({
    mutationFn: () => api<AcsFirmwareData>(path(deviceId), { method: "POST", body: JSON.stringify({ file_name: chosen || ready?.name || "", confirm }) }),
    onSuccess: (data) => {
      setConfirm("");
      client.setQueryData(key, data);
    },
  });
  const data = query.data;
  // The image of the newest catalog release, when already in the ACS (downloaded automatically or by hand), is preselected.
  const ready = data?.catalog ? data.files.find((file) => file.version === data.catalog?.latest) : undefined;
  const selected = chosen || ready?.name || "";
  if (!data || !data.linked) return null;
  const upgrade = data.upgrade;
  const running = upgrade?.state === "requested";
  const submitUpload = (event: FormEvent) => {
    event.preventDefault();
    upload.mutate();
  };
  const submitRun = (event: FormEvent) => {
    event.preventDefault();
    run.mutate();
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("acsFirmware.title")}</h2>
      <p className="ui-muted">{t("acsFirmware.help")}</p>
      <div className="ui-metrics">
        <div className="ui-metric"><span>{t("acsFirmware.current")}</span><strong className="ui-mono">{data.current_version ?? "—"}</strong>
          <small>{data.product_class ?? "—"}</small></div>
        <div className="ui-metric"><span>{t("acsFirmware.available")}</span><strong className="ui-mono">{data.catalog?.latest ?? "—"}</strong>
          <small>{data.catalog ? `${data.catalog.source}${data.catalog.released_at ? ` · ${formatDateTime(data.catalog.released_at, i18n.language)}` : ""}` : t("acsFirmware.noCatalog")}</small></div>
      </div>
      {data.catalog && (
        <p className={data.catalog.security ? "ui-alert ui-alert-warning" : "ui-muted"}>
          {data.catalog.newer ? t(data.catalog.security ? "acsFirmware.catalogSecurity" : "acsFirmware.catalogNewer", { version: data.catalog.latest }) : t("acsFirmware.catalogCurrent")}
          {data.catalog.notes_url && <> · <a href={data.catalog.notes_url} target="_blank" rel="noopener noreferrer">{t("acsFirmware.notes")}</a></>}
          {" · "}<Link to="/firmware/catalogs">{t("acsFirmware.catalogs")}</Link>
        </p>
      )}
      {data.catalog?.newer && ready && <div className="ui-alert ui-alert-success">{t("acsFirmware.ready", { version: data.catalog.latest })}</div>}
      {data.can_run && data.catalog?.newer && data.catalog.fetchable && !ready && (
        <div className="ui-row-actions">
          <button type="button" className="ui-button" disabled={fetchImage.isPending} onClick={() => data.catalog && fetchImage.mutate(data.catalog.latest)}>
            {fetchImage.isPending ? t("acsFirmware.fetching") : t("acsFirmware.fetch", { version: data.catalog.latest })}
          </button>
          {fetchImage.data && <small className="ui-live">{t("acsFirmware.fetched")}</small>}
          {fetchImage.error && <small className="ui-backup-error">{(fetchImage.error as Error).message}</small>}
        </div>
      )}
      {upgrade && (
        <div className={`ui-alert${upgrade.state === "failed" || upgrade.state === "unconfirmed" ? " ui-alert-warning" : upgrade.state === "verified" ? " ui-alert-success" : ""}`}>
          {t(`acsFirmware.states.${upgrade.state}`, {
            when: formatDateTime(upgrade.completed_at ?? upgrade.requested_at, i18n.language),
            from: upgrade.from_version ?? "—", to: upgrade.to_version ?? "—",
          })}
          {running && !upgrade.started ? ` · ${t("acsFirmware.waitingContact")}` : ""}
          {upgrade.state === "verified" && upgrade.matches === false ? ` · ${t("acsFirmware.notExpected", { version: upgrade.expected_version })}` : ""}
          {upgrade.fault ? <span className="ui-mono"> · {upgrade.fault.code} {upgrade.fault.message}</span> : null}
          {upgrade.task_removed ? ` · ${t("acsFirmware.taskRemoved")}` : ""}
          <small className="ui-muted"> · {upgrade.file_name}{upgrade.by ? ` · ${upgrade.by}` : ""}</small>
        </div>
      )}
      <TplinkCloudFirmware deviceId={deviceId} running={running} />
      {data.files_error && <div className="ui-alert ui-alert-warning">{data.files_error}</div>}
      {data.can_run && (
        <>
          <h3>{t("acsFirmware.uploadTitle")}</h3>
          <form className="ui-admin-form" onSubmit={submitUpload}>
            <label>
              <span>{t("acsFirmware.image", { max: data.max_mb })}</span>
              <input type="file" accept=".bin,.img,.w,.fw,application/octet-stream" onChange={(event) => setImage(event.target.files?.[0] ?? null)} />
            </label>
            <label>
              <span>{t("acsFirmware.version")}</span>
              <input value={version} onChange={(event) => setVersion(event.target.value)} placeholder={t("acsFirmware.versionPlaceholder")} spellCheck={false} />
            </label>
            <div className="ui-row-actions">
              <button type="submit" className="ui-button ui-button-ghost" disabled={!image || upload.isPending}>
                {upload.isPending ? t("acsFirmware.uploading") : t("acsFirmware.upload")}
              </button>
            </div>
          </form>
          {upload.error && <div className="ui-alert ui-alert-danger">{(upload.error as Error).message}</div>}
          <h3>{t("acsFirmware.runTitle")}</h3>
          {data.files.length === 0 ? (
            <p className="ui-muted">{t("acsFirmware.noFiles")}</p>
          ) : (
            <form className="ui-admin-form" onSubmit={submitRun}>
              <label>
                <span>{t("acsFirmware.file")}</span>
                <select value={selected} onChange={(event) => setChosen(event.target.value)} required>
                  <option value="">—</option>
                  {data.files.map((file) => (
                    <option key={file.name} value={file.name}>
                      {file.name} · {file.version || t("acsFirmware.noVersion")} · {mb(file.size)}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>{t("acsFirmware.confirm", { name: data.confirm_name })}</span>
                <input value={confirm} onChange={(event) => setConfirm(event.target.value)} autoComplete="off" spellCheck={false} />
              </label>
              <p className="ui-muted">{t("acsFirmware.warning")}</p>
              <div className="ui-row-actions">
                <button type="submit" className="ui-button ui-button-danger" disabled={!selected || confirm.trim() !== data.confirm_name || running || run.isPending}>
                  {t("acsFirmware.run")}
                </button>
              </div>
            </form>
          )}
          {run.error && <div className="ui-alert ui-alert-danger">{(run.error as Error).message}</div>}
        </>
      )}
    </section>
  );
}
