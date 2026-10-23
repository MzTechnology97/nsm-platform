import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";

interface FirmwareAuto { linked: boolean; enabled: boolean; fetched: number }

const PATH = "/api/ui/v1/admin/acs/firmware-auto";

/** Automatic download of newer official TP-Link images into the ACS (never installed without the operator). */
export function AcsFirmwareAutoSettings() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["admin", "acs", "firmware-auto"], queryFn: () => api<FirmwareAuto>(PATH) });
  const save = useMutation({
    mutationFn: (enabled: boolean) => api<FirmwareAuto>(PATH, { method: "POST", body: JSON.stringify({ enabled }) }),
    onSuccess: (data) => client.setQueryData(["admin", "acs", "firmware-auto"], data),
  });
  const data = query.data;
  if (!data || !data.linked) return null;
  return (
    <section className="ui-panel ui-admin-section" id="firmware-auto">
      <h2>{t("acsFirmwareAuto.title")}</h2>
      <p className="ui-muted">{t("acsFirmwareAuto.help")}</p>
      <label className="ui-with-brand">
        <input type="checkbox" checked={data.enabled} disabled={save.isPending} onChange={(event) => save.mutate(event.target.checked)} />{" "}
        <span>{t("acsFirmwareAuto.enabled")}</span>
      </label>
      <p className="ui-muted">{t("acsFirmwareAuto.fetched", { count: data.fetched })}</p>
      {save.error && <div className="ui-alert ui-alert-danger">{(save.error as Error).message}</div>}
    </section>
  );
}
