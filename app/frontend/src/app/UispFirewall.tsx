import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../api/client";
import { formatDateTime } from "../lib/format";
import { type AclRule, AclRuleEditor } from "./AclRuleEditor";

interface ManualRule { cmd: string; comment: string; enabled: boolean }
interface Rule { service: string; kind?: string; protocol: number; port: number; src: string | null; target: "ACCEPT" | "DROP"; comment: string; because?: string }
interface Plan {
  netmode: string;
  lan: string[];
  ports: { service: string; protocol: number; port: number }[];
  operator_rules: { comment: string | null; protocol: number | string | null; src: string | null; dport: number | string | null; target: string | null; enabled: boolean }[];
  add: Rule[];
  skipped: Rule[];
  open_to_all: string[];
  blocker: string | null;
  not_protected: { service: string; protocol: number; port: number; reason: string; comment?: string }[];
  dmz: boolean;
  nsm_rules: number;
  up_to_date: boolean;
  manual: ManualRule[];
  backup: { id: string; timestamp: string | null };
}
interface FirewallState {
  name: string;
  state: { status: string; applied_at: string; by: string | null; rules_added: number; skipped: number; restored_at?: string } | null;
  can_restore: boolean;
  can_configure: boolean;
  plan: Plan | null;
  error: string | null;
  services: string[];
  common_rules: unknown[];
  device_rules: AclRule[];
  outdated: boolean;
}

const proto = (value: number | string | null) => (value === 6 ? "TCP" : value === 17 ? "UDP" : value ?? "—");

function message(error: unknown) {
  return error instanceof ApiError ? error.message : (error as Error | null)?.message;
}

/** UISP tab of an airOS CPE: firewall rules that limit the services of the common ACL rules to the management networks and the LAN, plus the extra rules. */
export function UispFirewallPanel({ id }: { id: string }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["device", id, "uisp-firewall"],
    queryFn: () => api<FirewallState>(`/api/ui/v1/devices/${id}/uisp/firewall`),
    retry: false,
    placeholderData: (previous) => previous,
  });
  const [typed, setTyped] = useState("");
  // The hand-written rules as edited here (null: as on the CPE); they go with the next apply.
  const [manual, setManual] = useState<ManualRule[] | null>(null);
  const original = query.data?.plan?.manual ?? [];
  const manualDirty = manual !== null && JSON.stringify(manual) !== JSON.stringify(original);
  const done = () => { setTyped(""); setManual(null); void client.invalidateQueries({ queryKey: ["device", id] }); };
  const apply = useMutation({
    mutationFn: (confirmation: string) => api(`/api/ui/v1/devices/${id}/uisp/firewall`, {
      method: "POST",
      body: JSON.stringify({ confirmation, ...(manualDirty ? { manual, manual_seen: original.map((rule) => rule.cmd) } : {}) }),
    }),
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
      <p className="ui-muted">
        {t("uispFirewall.servicesFrom", { services: data.services.map((service) => t(`uispFirewall.service.${service}`)).join(", "),
          common: data.common_rules.length, device: data.device_rules.length })}{" "}
        <Link to="/acl">{t("acl.title")}</Link>
      </p>
      {data.outdated && <div className="ui-alert ui-alert-warning">{t("uispFirewall.outdated")}</div>}
      <DeviceRules id={id} rules={data.device_rules} canEdit={data.can_configure} />
      {plan && (
        <>
          <p className="ui-muted">{t("uispFirewall.from", { mode: plan.netmode, lan: plan.lan.join(", ") || "—", nsm: plan.nsm_rules })}</p>
          {plan.blocker && <div className="ui-alert ui-alert-warning">{plan.blocker}</div>}
          {plan.dmz && <div className="ui-alert ui-alert-warning">{t("uispFirewall.dmz")}</div>}
          {plan.not_protected.length > 0 && (
            <div className="ui-alert ui-alert-warning">
              {t("uispFirewall.notProtected")}
              <ul>{plan.not_protected.map((item, index) => <li key={index}>{proto(item.protocol)}/{item.port} · {item.service === "custom" ? item.comment || t("uispFirewall.kind.custom") : t(`uispFirewall.service.${item.service}`)}: {item.reason}</li>)}</ul>
            </div>
          )}
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
                      <td className="ui-mono">{proto(rule.protocol)}/{rule.port} · {rule.service === "custom" ? t(`uispFirewall.kind.${rule.kind}`) : rule.service}</td>
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
      {plan && !plan.blocker && (
        <ManualRules rules={manual ?? original} onChange={setManual} dirty={manualDirty} readOnly={!data.can_configure} />
      )}
      {data.can_configure && plan && !plan.blocker && ((plan.add.length > 0 && !plan.up_to_date) || manualDirty) && (
        <form className="ui-admin-form ui-admin-stack" onSubmit={submit}>
          <div className="ui-alert ui-alert-warning">{t("uispFirewall.reboot")}</div>
          {manualDirty && <p>{t("acl.manual.withApply")}</p>}
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

/** Extra rules of this CPE only, kept in NSM: saved here, written on the CPE with the next apply. */
function DeviceRules({ id, rules, canEdit }: { id: string; rules: AclRule[]; canEdit: boolean }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [draft, setDraft] = useState<AclRule[] | null>(null);
  const current = draft ?? rules;
  const save = useMutation({
    mutationFn: () => api<{ device_rules: AclRule[] }>(`/api/ui/v1/devices/${id}/uisp/firewall/rules`, { method: "PUT", body: JSON.stringify({ rules: current }) }),
    onSuccess: () => {
      setDraft(null);
      void client.invalidateQueries({ queryKey: ["device", id] });
      void client.invalidateQueries({ queryKey: ["security"] });
    },
  });
  if (!canEdit && rules.length === 0) return null;
  return (
    <div className="ui-admin-stack">
      <h3>{t("acl.device.title")}</h3>
      <p className="ui-muted">{t("acl.device.hint")}</p>
      <AclRuleEditor rules={current} onChange={setDraft} max={32} readOnly={!canEdit} />
      {canEdit && draft !== null && (
        <div className="ui-row-actions">
          <button type="button" className="ui-button" disabled={save.isPending} onClick={() => save.mutate()}>{t("acl.device.save")}</button>
          <button type="button" className="ui-button ui-button-ghost" onClick={() => setDraft(null)}>{t("customerForm.cancel")}</button>
          {save.error && <small className="ui-backup-error">{message(save.error)}</small>}
        </div>
      )}
    </div>
  );
}

/** Rules written by hand on the CPE (not NSM's), in their order: changed, switched off or removed here, applied with the next apply. */
function ManualRules({ rules, onChange, dirty, readOnly }: { rules: ManualRule[]; onChange: (rules: ManualRule[] | null) => void; dirty: boolean; readOnly: boolean }) {
  const { t } = useTranslation();
  if (rules.length === 0 && !dirty) return null;
  const set = (index: number, patch: Partial<ManualRule>) => onChange(rules.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)));
  return (
    <div className="ui-admin-stack">
      <h3>{t("acl.manual.title")}</h3>
      <p className="ui-muted">{t("acl.manual.hint")}</p>
      {rules.length > 0 ? (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead>
              <tr>{["enabled", "cmd", "comment"].map((key) => <th key={key}><span className="ui-th">{t(`acl.manual.columns.${key}`)}</span></th>)}<th /></tr>
            </thead>
            <tbody>
              {rules.map((rule, index) => (
                <tr key={index}>
                  <td><input type="checkbox" checked={rule.enabled} disabled={readOnly} onChange={(event) => set(index, { enabled: event.target.checked })}
                    aria-label={t("acl.manual.columns.enabled")} /></td>
                  <td><input className="ui-mono" value={rule.cmd} readOnly={readOnly} size={48} onChange={(event) => set(index, { cmd: event.target.value })}
                    aria-label={t("acl.manual.columns.cmd")} /></td>
                  <td><input value={rule.comment} readOnly={readOnly} maxLength={32} onChange={(event) => set(index, { comment: event.target.value })}
                    aria-label={t("acl.manual.columns.comment")} /></td>
                  <td>{!readOnly && (
                    <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={() => onChange(rules.filter((_, i) => i !== index))}>
                      {t("acl.rules.remove")}
                    </button>
                  )}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="ui-muted">{t("acl.manual.allRemoved")}</p>
      )}
      {dirty && (
        <div className="ui-row-actions">
          <button type="button" className="ui-button ui-button-ghost" onClick={() => onChange(null)}>{t("acl.manual.reset")}</button>
        </div>
      )}
    </div>
  );
}
