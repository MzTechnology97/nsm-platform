import { Link } from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

import { useIcmpSettings, useSaveIcmp } from "../api/monitor";

/** Small window opened from the "Ping da NSM" tile: switch the ping from NSM on or off; thresholds stay on the Monitor tab. */
export function PingToggleDialog({ deviceId, onClose }: { deviceId: string; onClose: () => void }) {
  const { t } = useTranslation();
  const query = useIcmpSettings(deviceId);
  const save = useSaveIcmp(deviceId);
  const data = query.data;
  const toggle = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  useEffect(() => toggle.current?.focus(), [data]);

  const change = (enabled: boolean) => {
    if (!data) return;
    save.mutate({ enabled, target: data.target ?? "", rtt_ms: data.rtt_ms?.toString() ?? "", loss_pct: data.loss_pct?.toString() ?? "" });
  };
  const enabled = save.isPending && save.variables ? save.variables.enabled : !!data?.enabled;
  const target = data?.effective_target ?? data?.management_ip ?? null;

  return (
    <div className="ui-dialog-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="ui-dialog" role="dialog" aria-modal="true" aria-labelledby="ui-ping-toggle-title">
        <div className="ui-page-header-row">
          <h2 id="ui-ping-toggle-title">{t("device.pingToggle.title")}</h2>
          <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={onClose}>{t("device.pingToggle.close")}</button>
        </div>
        {query.error ? (
          <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
        ) : !data ? (
          <p className="ui-muted">{t("app.loading")}</p>
        ) : (
          <>
            <p className="ui-muted">{t("device.pingToggle.help")}</p>
            <label className="ui-switch">
              <input ref={toggle} type="checkbox" role="switch" checked={enabled} disabled={!data.can_edit || save.isPending || (!enabled && !target)}
                onChange={(event) => change(event.target.checked)} />
              <span className="ui-switch-track" aria-hidden="true"><span className="ui-switch-thumb" /></span>
              <span>{enabled ? t("device.pingToggle.on") : t("device.pingToggle.off")}</span>
            </label>
            <p className="ui-muted">{target ? t("device.pingToggle.target", { target }) : t("device.pingToggle.noTarget")}</p>
            {!data.globally_enabled && <div className="ui-alert ui-alert-warning">{t("monitor.ping.globallyOff")}</div>}
            {!data.can_edit && <p className="ui-key-hint">{t("device.pingToggle.readOnly")}</p>}
            {save.isSuccess && <small className="ui-live">{save.data?.enabled ? t("device.pingToggle.savedOn") : t("device.pingToggle.savedOff")}</small>}
            {save.error && <small className="ui-backup-error">{(save.error as Error).message}</small>}
            <Link to="/devices/$deviceId/monitor" params={{ deviceId }} onClick={onClose}>{t("device.pingToggle.more")}</Link>
          </>
        )}
      </div>
    </div>
  );
}
