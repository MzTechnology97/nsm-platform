import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { AdminTabs } from "../app/AdminTabs";
import { IntegrationTabs } from "../app/IntegrationTabs";
import { Loading } from "../app/States";
import { formatDateTime } from "../lib/format";

interface ProbeAdmin {
  configured: boolean;
  enabled: boolean;
  base_url: string;
  name: string;
  verify_tls: boolean;
  last_test_status: string | null;
  last_error: string | null;
  last_tested_at: string | null;
}

const URL = "/api/ui/v1/admin/integrations/probe";
// probe/install.sh: installs Docker when missing, downloads the probe, writes the secret and starts it.
const INSTALL_COMMANDS = [
  "curl -fsSL https://raw.githubusercontent.com/MzTechnology97/nsm-platform/main/probe/install.sh -o install.sh",
  "sudo bash install.sh --allow <NSM-public-IP> --name probe-1",
  "# HTTPS through Caddy: add --domain probe.example.net",
  "# later: sudo bash install.sh status | update | show-secret | uninstall",
].join("\n");

/** External probe (SCAN-02): where it is, the shared secret, test. */
export function AdminProbe() {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["admin-probe"], queryFn: () => api<ProbeAdmin>(URL) });
  const refresh = () => void client.invalidateQueries({ queryKey: ["admin-probe"] });
  const save = useMutation({ mutationFn: (body: object) => api<ProbeAdmin>(URL, { method: "POST", body: JSON.stringify(body) }), onSuccess: refresh });
  const test = useMutation({ mutationFn: () => api<{ ok: boolean; message: string }>(`${URL}/test`, { method: "POST" }), onSuccess: refresh });
  const data = query.data;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("probeAdmin.title")}</h1>
          <p>{t("probeAdmin.subtitle")}</p>
        </div>
      </header>
      <AdminTabs />
      <IntegrationTabs />
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : (
        <div className="ui-two-columns">
          <Form data={data} save={save} />
          <section className="ui-panel ui-admin-section">
            <h2>{t("probeAdmin.state")}</h2>
            <p>
              <span className={`ui-status ui-status-${data.enabled && data.last_test_status === "success" ? "online" : data.last_test_status === "failed" ? "offline" : "unknown"}`}>
                {!data.configured ? t("probeAdmin.notConfigured") : !data.enabled ? t("probeAdmin.off") : data.last_test_status === "success" ? t("probeAdmin.ok") : data.last_test_status === "failed" ? t("probeAdmin.failed") : t("probeAdmin.untested")}
              </span>
            </p>
            {data.last_tested_at && <small className="ui-muted ui-block">{t("probeAdmin.lastTest", { when: formatDateTime(data.last_tested_at, i18n.language) })}</small>}
            {data.last_error && <div className="ui-alert ui-alert-warning">{data.last_error}</div>}
            <button type="button" className="ui-button ui-button-ghost" disabled={!data.configured || test.isPending} onClick={() => test.mutate()}>{t("probeAdmin.test")}</button>
            {test.data && <p className={test.data.ok ? "ui-live" : "ui-backup-error"}>{test.data.message}</p>}
            <h3>{t("probeAdmin.howTitle")}</h3>
            <ol className="ui-admin-stack">
              {["host", "secret", "run", "sources", "connect"].map((key) => <li key={key}>{t(`probeAdmin.how.${key}`)}</li>)}
            </ol>
            <pre className="ui-code-block"><code>{INSTALL_COMMANDS}</code></pre>
          </section>
        </div>
      )}
    </section>
  );
}

function Form({ data, save }: { data: ProbeAdmin; save: ReturnType<typeof useMutation<ProbeAdmin, Error, object>> }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({ base_url: data.base_url, name: data.name, secret: "", verify_tls: data.verify_tls, enabled: data.configured ? data.enabled : true });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(form, { onSuccess: () => setForm({ ...form, secret: "" }) });
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("probeAdmin.connection")}</h2>
      <p className="ui-muted">{t("probeAdmin.connectionHelp")}</p>
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit} autoComplete="off">
        <label><span>{t("probeAdmin.url")}</span><input type="url" required value={form.base_url} placeholder="https://probe.example.net:8788" onChange={(event) => setForm({ ...form, base_url: event.target.value })} /></label>
        <label><span>{t("probeAdmin.name")}</span><input value={form.name} placeholder="probe-1" onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
        <label>
          <span>{t("probeAdmin.secret")}</span>
          <input type="password" value={form.secret} autoComplete="off" placeholder={data.configured ? t("notificationsAdmin.keepSecret") : ""} onChange={(event) => setForm({ ...form, secret: event.target.value })} />
        </label>
        <label className="ui-with-brand"><input type="checkbox" checked={form.verify_tls} onChange={(event) => setForm({ ...form, verify_tls: event.target.checked })} /> <span>{t("cnmaestro.verifyTls")}</span></label>
        <label className="ui-with-brand"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /> <span>{t("probeAdmin.enabled")}</span></label>
        <button type="submit" className="ui-button" disabled={save.isPending}>{t("cnmaestro.save")}</button>
        {save.isSuccess && <small className="ui-live">{t("probeAdmin.saved")}</small>}
        {save.error && <small className="ui-backup-error">{(save.error as Error).message}</small>}
      </form>
    </section>
  );
}
