import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../api/client";
import { formatDateTime } from "../lib/format";

interface Rule { service: string; protocol: number; port: number; src: string | null; target: "ACCEPT" | "DROP"; comment: string; because?: string }
interface Plan {
  netmode: string;
  lan: string[];
  ports: { service: string; protocol: number; port: number }[];
  operator_rules: { comment: string | null; protocol: number | string | null; src: string | null; dport: number | string | null; target: string | null; enabled: boolean }[];
  add: Rule[];
  skipped: Rule[];
  open_to_all: string[];
  blocker: string | null;
  nsm_rules: number;
  up_to_date: boolean;
  backup: { id: string; timestamp: string | null };
}
interface FirewallState {
  name: string;
  state: { status: string; applied_at: string; by: string | null; rules_added: number; skipped: number; restored_at?: string } | null;
  can_restore: boolean;
  can_configure: boolean;
  plan: Plan | null;
  error: string | null;
}

const proto = (value: number | string | null) => (value === 6 ? "TCP" : value === 17 ? "UDP" : value ?? "—");

function message(error: unknown) {
  return error instanceof ApiError ? error.message : (error as Error | null)?.message;
}

/** UISP tab of an airOS CPE: firewall rules that limit SSH, SNMP and discovery to the management networks and the LAN. */
export function UispFirewallPanel({ id }: { id: string }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["device", id, "uisp-firewall"], queryFn: () => api<FirewallState>(`/api/ui/v1/devices/${id}/uisp/firewall`), retry: false });
  const [typed, setTyped] = useState("");
  const done = () => { setTyped(""); void client.invalidateQueries({ queryKey: ["device", id] }); };
  const apply = useMutation({
    mutationFn: (confirmation: string) => api(`/api/ui/v1/devices/${id}/uisp/firewall`, { method: "POST", body: JSON.stringify({ confirmation }) }),
    onSuccess: done,
  });
  const restore = useMutation({
    mutationFn: (confirmation: string) => api(`/api/ui/v1/devices/${id}/uisp/firewall/restore`, { method: "POST", body: JSON.stringify({ confirmation }) }),
    onSuccess: done,
  });
  const data = query.data;
  if (!data) return null;
  const plan = data.plan;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    apply.mutate(typed);
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("uispFirewall.title")}</h2>
      <p className="ui-muted">{t("uispFirewall.hint")}</p>
      {data.state && (
        <p>{data.state.status === "restored"
          ? t("uispFirewall.restored", { when: formatDateTime(data.state.restored_at ?? data.state.applied_at, i18n.language) })
          : t("uispFirewall.applied", { when: formatDateTime(data.state.applied_at, i18n.language), by: data.state.by ?? "—", added: data.state.rules_added, skipped: data.state.skipped })}</p>
      )}
      {data.error && <div className="ui-alert ui-alert-warning">{data.error}</div>}
      {plan && (
        <>
          <p className="ui-muted">{t("uispFirewall.from", { mode: plan.netmode, lan: plan.lan.join(", ") || "—", nsm: plan.nsm_rules })}</p>
          {plan.blocker && <div className="ui-alert ui-alert-warning">{plan.blocker}</div>}
          {plan.open_to_all.length > 0 && <div className="ui-alert ui-alert-warning">{t("uispFirewall.openToAll", { services: plan.open_to_all.join(", ") })}</div>}
          {plan.up_to_date && <p className="ui-live">{t("uispFirewall.upToDate", { count: plan.nsm_rules })}</p>}
          {plan.add.length > 0 && !plan.up_to_date && (
            <div className="ui-data-wrap">
              <table className="ui-data-table">
                <thead><tr>{["target", "service", "source", "comment"].map((key) => <th key={key}><span className="ui-th">{t(`uispFirewall.columns.${key}`)}</span></th>)}</tr></thead>
                <tbody>
                  {plan.add.map((rule, index) => (
                    <tr key={index}>
                      <td><span className={rule.target === "ACCEPT" ? "ui-severity" : "ui-severity ui-severity-warning"}>{rule.target}</span></td>
                      <td className="ui-mono">{proto(rule.protocol)}/{rule.port} · {rule.service}</td>
                      <td className="ui-mono">{rule.src ?? t("uispFirewall.everyoneElse")}</td>
                      <td>{rule.comment}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {plan.add.length === 0 && !plan.blocker && <p className="ui-live">{t("uispFirewall.nothingToAdd")}</p>}
          {plan.skipped.length > 0 && (
            <details>
              <summary>{t("uispFirewall.skipped", { count: plan.skipped.length })}</summary>
              <ul className="ui-admin-stack">
                {plan.skipped.map((rule, index) => (
                  <li key={index}>{rule.target} {proto(rule.protocol)}/{rule.port} {rule.src ?? ""} — {t("uispFirewall.because", { rule: rule.because ?? "" })}</li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
      {data.can_configure && plan && !plan.blocker && plan.add.length > 0 && !plan.up_to_date && (
        <form className="ui-admin-form ui-admin-stack" onSubmit={submit}>
          <div className="ui-alert ui-alert-warning">{t("uispFirewall.reboot")}</div>
          <label>
            <span>{t("uispFirewall.confirm", { name: data.name })}</span>
            <input value={typed} onChange={(event) => setTyped(event.target.value)} autoComplete="off" />
          </label>
          <button type="submit" className="ui-button" disabled={apply.isPending || typed.trim() !== data.name}>
            {apply.isPending ? t("uispFirewall.working") : t("uispFirewall.apply")}
          </button>
          {apply.isSuccess && <small className="ui-live">{t("uispFirewall.done")}</small>}
          {apply.error && <small className="ui-backup-error">{message(apply.error)}</small>}
        </form>
      )}
      {data.can_configure && data.can_restore && (
        <div className="ui-row-actions">
          <button type="button" className="ui-button ui-button-ghost" disabled={restore.isPending || typed.trim() !== data.name} onClick={() => restore.mutate(typed)}>
            {t("uispFirewall.restore")}
          </button>
          {restore.error && <small className="ui-backup-error">{message(restore.error)}</small>}
        </div>
      )}
    </section>
  );
}
