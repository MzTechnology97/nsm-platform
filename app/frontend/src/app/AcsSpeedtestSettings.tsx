import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";

interface SpeedtestSettings { linked: boolean; server_url: string; download_url: string; upload_url: string; size_mb: number; min_mb: number; max_mb: number }

const PATH = "/api/ui/v1/admin/acs/speedtest";

/** Where TR-069 CPEs run their speed test (Amministrazione → ACS). */
export function AcsSpeedtestSettings() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["admin", "acs", "speedtest"], queryFn: () => api<SpeedtestSettings>(PATH) });
  const [form, setForm] = useState({ server_url: "", download_url: "", upload_url: "", size_mb: 25 });
  useEffect(() => {
    if (query.data) setForm({ server_url: query.data.server_url, download_url: query.data.download_url, upload_url: query.data.upload_url, size_mb: query.data.size_mb });
  }, [query.data]);
  const save = useMutation({
    mutationFn: () => api<SpeedtestSettings>(PATH, { method: "POST", body: JSON.stringify(form) }),
    onSuccess: (data) => client.setQueryData(["admin", "acs", "speedtest"], data),
  });
  const data = query.data;
  if (!data || !data.linked) return null;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };
  const field = (name: "server_url" | "download_url" | "upload_url") => (
    <label>
      <span>{t(`acsSpeedtest.settings.${name}`)}</span>
      <input value={form[name]} onChange={(event) => setForm({ ...form, [name]: event.target.value })} placeholder={t(`acsSpeedtest.settings.${name}Placeholder`)} spellCheck={false} />
    </label>
  );
  return (
    <section className="ui-panel ui-admin-section" id="speedtest">
      <h2>{t("acsSpeedtest.settings.title")}</h2>
      <p className="ui-muted">{t("acsSpeedtest.settings.help")}</p>
      <form className="ui-admin-form" onSubmit={submit}>
        {field("server_url")}
        {field("download_url")}
        {field("upload_url")}
        <label>
          <span>{t("acsSpeedtest.settings.size")}</span>
          <input type="number" min={data.min_mb} max={data.max_mb} value={form.size_mb} onChange={(event) => setForm({ ...form, size_mb: Number(event.target.value) })} />
        </label>
        <div className="ui-row-actions">
          <button type="submit" className="ui-button" disabled={save.isPending}>{t("acsSpeedtest.settings.save")}</button>
        </div>
      </form>
      {save.isSuccess && <div className="ui-alert ui-alert-success">{t("acsSpeedtest.settings.saved")}</div>}
      {save.error && <div className="ui-alert ui-alert-danger">{(save.error as Error).message}</div>}
    </section>
  );
}
