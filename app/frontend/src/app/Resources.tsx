import { useTranslation } from "react-i18next";

import type { DeviceRow } from "../api/devices";

/** CPU and memory use as two small bars (amber from 75%, red from 90%); faded when the device is offline (last known values). */
export function Resources({ telemetry, online }: { telemetry: DeviceRow["telemetry"]; online: boolean }) {
  const { t } = useTranslation();
  if (!telemetry || (telemetry.cpu == null && telemetry.ram == null)) return <span className="ui-muted">—</span>;
  const bar = (label: string, value: number | null) =>
    value == null ? null : (
      <span className="ui-meter" title={`${label} ${Math.round(value)}%`}>
        <span className="ui-meter-label">{label}</span>
        <svg className="ui-meter-track" viewBox="0 0 100 6" preserveAspectRatio="none" aria-hidden="true">
          <rect width="100" height="6" rx="3" className="ui-meter-bg" />
          <rect width={Math.max(2, Math.min(100, value))} height="6" rx="3" className={`ui-meter-fill${value >= 90 ? " is-danger" : value >= 75 ? " is-warning" : ""}`} />
        </svg>
        <span className="ui-meter-value">{Math.round(value)}%</span>
      </span>
    );
  return (
    <span className={`ui-resources${online ? "" : " is-stale"}`}>
      {bar(t("devices.cpu"), telemetry.cpu)}
      {bar(t("devices.ram"), telemetry.ram)}
    </span>
  );
}
