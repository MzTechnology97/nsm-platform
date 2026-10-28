import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { formatDateTime } from "../lib/format";

interface Capability {
  id: string;
  status: "available" | "rpc" | "not_exposed" | "unsupported";
  reason: string | null;
  paths: string[];
  writes: boolean;
  where: string | null;
}

interface CapabilitiesData {
  data_model: string | null;
  vendor_profile: string | null;
  manufacturer: string | null;
  product_class: string | null;
  last_inform: string | null;
  snapshots: number;
  reason: string | null;
  capabilities: Capability[];
}

const STATUS_CLASS: Record<Capability["status"], string> = {
  available: "ui-status ui-status-success",
  rpc: "ui-status ui-status-success",
  not_exposed: "ui-status ui-status-warning",
  unsupported: "ui-status ui-status-offline",
};

/** Which NSM capabilities the CPE offers and why, from its TR-098 / TR-181 parameter profile (read only). */
export function AcsCapabilities({ deviceId }: { deviceId: string }) {
  const { t, i18n } = useTranslation();
  const query = useQuery({
    queryKey: ["device", deviceId, "acs-capabilities"],
    queryFn: () => api<CapabilitiesData>(`/api/ui/v1/devices/${encodeURIComponent(deviceId)}/acs/capabilities`),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  if (query.error) return null;
  const data = query.data;
  if (!data) return null;
  const model = data.data_model === "tr181" ? "TR-181" : data.data_model === "tr098" ? "TR-098" : "—";
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("acsCapabilities.title")}</h2>
      <p className="ui-muted">
        {t("acsCapabilities.help", { model, vendor: data.vendor_profile ? t(`acsCapabilities.vendors.${data.vendor_profile}`, { defaultValue: data.vendor_profile })
          : t("acsCapabilities.vendors.generic"), when: formatDateTime(data.last_inform, i18n.language) })}
      </p>
      {data.reason === "not_read" ? (
        <p className="ui-muted">{t("acsCapabilities.notRead")}</p>
      ) : (
        <div className="ui-table-scroll">
          <table className="ui-stats-table">
            <thead>
              <tr>
                <th>{t("acsCapabilities.columns.capability")}</th>
                <th>{t("acsCapabilities.columns.status")}</th>
                <th>{t("acsCapabilities.columns.why")}</th>
                <th>{t("acsCapabilities.columns.writes")}</th>
              </tr>
            </thead>
            <tbody>
              {data.capabilities.map((cap) => (
                <tr key={cap.id}>
                  <td>{t(`acsCapabilities.caps.${cap.id}`, { defaultValue: cap.id })}</td>
                  <td><span className={STATUS_CLASS[cap.status]}>{t(`acsCapabilities.status.${cap.status}`)}</span></td>
                  <td>
                    {cap.reason ? t(`acsCapabilities.reasons.${cap.reason}`, { defaultValue: cap.reason, count: data.snapshots }) : t("acsCapabilities.reasons.found")}
                    {cap.paths.length > 0 && <div className="ui-mono ui-muted">{cap.paths.join(", ")}</div>}
                  </td>
                  <td>{cap.writes ? t("acsCapabilities.writesYes") : t("acsCapabilities.writesNo")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
