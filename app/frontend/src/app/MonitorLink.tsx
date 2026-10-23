import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

/** Charts live in the Monitor tab for every kind of device; source tabs (ACS, UISP, cnMaestro) point there. */
export function MonitorLink({ deviceId }: { deviceId: string }) {
  const { t } = useTranslation();
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("monitor.source.chartsTitle")}</h2>
      <p className="ui-muted">
        {t("monitor.source.chartsMoved")} <Link to="/devices/$deviceId/monitor" params={{ deviceId }}>{t("monitor.source.openMonitor")}</Link>
      </p>
    </section>
  );
}
