import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";

interface AlertSettings {
  linked: boolean;
  enabled: boolean;
  offline: boolean;
  wan_down: boolean;
  reconnects_per_hour: number;
  reboots_per_day: number;
  snr_min_db: number;
  dsl_rate_drop_percent: number;
  pon_rx_min_dbm: number;
}

type Form = Omit<AlertSettings, "linked">;
const PATH = "/api/ui/v1/admin/acs/alerts";
const SWITCHES = ["enabled", "offline", "wan_down"] as const;
const NUMBERS = [["reconnects_per_hour", 1, 0], ["reboots_per_day", 1, 0], ["snr_min_db", 0.5, 0], ["dsl_rate_drop_percent", 5, 0],
  ["pon_rx_min_dbm", 0.5, -40]] as const;

/** Alerts raised for TR-069 CPEs (Amministrazione → ACS): switches and thresholds; 0 disables a threshold. */
export function AcsAlertSettings() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["admin", "acs", "alerts"], queryFn: () => api<AlertSettings>(PATH) });
  const [form, setForm] = useState<Form | null>(null);
  useEffect(() => {
    if (query.data) {
      const { linked: _linked, ...rest } = query.data;
      setForm(rest);
    }
  }, [query.data]);
  const save = useMutation({
    mutationFn: () => api<AlertSettings>(PATH, { method: "POST", body: JSON.stringify(form) }),
    onSuccess: (data) => client.setQueryData(["admin", "acs", "alerts"], data),
  });
  if (!query.data || !query.data.linked || !form) return null;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };
  return (
    <section className="ui-panel ui-admin-section" id="alerts">
      <h2>{t("acsAlerts.title")}</h2>
      <p className="ui-muted">{t("acsAlerts.help")}</p>
      <form className="ui-admin-form" onSubmit={submit}>
        {SWITCHES.map((key) => (
          <label key={key} className="ui-with-brand">
            <input type="checkbox" checked={form[key]} onChange={(event) => setForm({ ...form, [key]: event.target.checked })} />{" "}
            <span>{t(`acsAlerts.fields.${key}`)}</span>
          </label>
        ))}
        {NUMBERS.map(([key, step, min]) => (
          <label key={key}>
            <span>{t(`acsAlerts.fields.${key}`)}</span>
            <input type="number" min={min} step={step} value={form[key]} onChange={(event) => setForm({ ...form, [key]: Number(event.target.value) })} />
          </label>
        ))}
        <div className="ui-row-actions">
          <button type="submit" className="ui-button" disabled={save.isPending}>{t("acsAlerts.save")}</button>
        </div>
      </form>
      {save.isSuccess && <div className="ui-alert ui-alert-success">{t("acsAlerts.saved")}</div>}
      {save.error && <div className="ui-alert ui-alert-danger">{(save.error as Error).message}</div>}
    </section>
  );
}
