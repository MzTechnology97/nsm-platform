import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { formatDateTime } from "../lib/format";

interface CloudOffer { version: string; released_at: string | null; notes: string | null; url: string | null; newer: boolean; progress: number | null; status: number | null }

interface CloudFirmwareData {
  supported: boolean;
  offer: CloudOffer | null;
  current_version: string | null;
  checked_at: string | null;
  last_inform: string | null;
  can_run: boolean;
  confirm_name: string;
  error: string | null;
}

const path = (id: string) => `/api/ui/v1/devices/${encodeURIComponent(id)}/acs/firmware/cloud`;

/** Firmware offered by TP-Link's cloud to the CPE, and the CPE's own upgrade from there (TR-069 vendor parameters). */
export function TplinkCloudFirmware({ deviceId, running }: { deviceId: string; running: boolean }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const key = ["device", deviceId, "acs-firmware-cloud"];
  const query = useQuery({ queryKey: key, queryFn: () => api<CloudFirmwareData>(path(deviceId)) });
  const [confirm, setConfirm] = useState("");
  const check = useMutation({
    mutationFn: () => api<CloudFirmwareData>(`${path(deviceId)}/check`, { method: "POST" }),
    onSuccess: (data) => client.setQueryData(key, data),
  });
  const run = useMutation({
    mutationFn: () => api<CloudFirmwareData>(path(deviceId), { method: "POST", body: JSON.stringify({ confirm }) }),
    onSuccess: (data) => {
      setConfirm("");
      client.setQueryData(key, data);
      // The upgrade is followed by the firmware box above, like an image upgrade.
      void client.invalidateQueries({ queryKey: ["device", deviceId, "acs-firmware"] });
    },
  });
  const data = query.data;
  if (!data || !data.supported) return null;
  const offer = data.offer;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    run.mutate();
  };
  return (
    <div className="ui-tplink-cloud">
      <h3>{t("acsFirmware.cloud.title")}</h3>
      <p className="ui-muted">{t("acsFirmware.cloud.help")}</p>
      {data.error && <div className="ui-alert ui-alert-warning">{data.error}</div>}
      {offer ? (
        <p className={offer.newer ? "ui-alert ui-alert-warning" : "ui-muted"}>
          {offer.newer ? t("acsFirmware.cloud.newer", { version: offer.version }) : t("acsFirmware.cloud.current")}
          {offer.released_at ? ` · ${t("acsFirmware.cloud.released", { date: offer.released_at })}` : ""}
        </p>
      ) : (
        <p className="ui-muted">{t("acsFirmware.cloud.none")}</p>
      )}
      {offer?.notes && (
        <details>
          <summary>{t("acsFirmware.notes")}</summary>
          <p className="ui-tplink-cloud-notes">{offer.notes}</p>
        </details>
      )}
      <small className="ui-muted">
        {data.checked_at ? t("acsFirmware.cloud.checked", { when: formatDateTime(data.checked_at, i18n.language) }) : t("acsFirmware.cloud.neverChecked")}
      </small>
      {data.can_run && (
        <div className="ui-row-actions">
          <button type="button" className="ui-button ui-button-ghost" disabled={check.isPending} onClick={() => check.mutate()}>
            {check.isPending ? t("acsFirmware.cloud.checking") : t("acsFirmware.cloud.check")}
          </button>
        </div>
      )}
      {check.error && <div className="ui-alert ui-alert-danger">{(check.error as Error).message}</div>}
      {data.can_run && offer?.newer && (
        <form className="ui-admin-form" onSubmit={submit}>
          <label>
            <span>{t("acsFirmware.confirm", { name: data.confirm_name })}</span>
            <input value={confirm} onChange={(event) => setConfirm(event.target.value)} autoComplete="off" spellCheck={false} />
          </label>
          <p className="ui-muted">{t("acsFirmware.cloud.warning")}</p>
          <div className="ui-row-actions">
            <button type="submit" className="ui-button ui-button-danger" disabled={confirm.trim() !== data.confirm_name || running || run.isPending}>
              {t("acsFirmware.cloud.run", { version: offer.version })}
            </button>
          </div>
        </form>
      )}
      {run.error && <div className="ui-alert ui-alert-danger">{(run.error as Error).message}</div>}
    </div>
  );
}
