import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { type BackupPolicyRow, useBackupPolicies, usePolicyWrite } from "../api/backupPolicies";

type Show = "all" | "on" | "off";

/**
 * Every backup policy in one table, with the devices it protects and the actions (edit, duplicate, turn on/off,
 * delete). Inside a customer only the policies that concern it: its own, and global/vendor ones covering its devices.
 */
export function BackupPolicyList({ customerId }: { customerId?: string }) {
  const { t } = useTranslation();
  const query = useBackupPolicies(customerId);
  const [text, setText] = useState("");
  const [show, setShow] = useState<Show>("all");
  const data = query.data;
  if (query.error) return <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>;
  if (!data) return <p className="ui-muted">{t("app.loading")}</p>;
  const needle = text.trim().toLowerCase();
  const rows = data.items.filter((row) => (show === "all" || (show === "on") === row.enabled)
    && (!needle || [row.name, row.scope, row.description ?? ""].some((value) => value.toLowerCase().includes(needle))));
  const counts = { all: data.items.length, on: data.items.filter((row) => row.enabled).length, off: data.items.filter((row) => !row.enabled).length };
  return (
    <>
      <div className="ui-filter-bar">
        <div className="ui-chips" role="group" aria-label={t("backupPolicies.title")}>
          {(["all", "on", "off"] as const).map((key) => (
            <button key={key} type="button" className={`ui-chip${show === key ? " is-active" : ""}`} aria-pressed={show === key} onClick={() => setShow(key)}>
              {t(`backupPolicies.show.${key}`)} <span className="ui-chip-count">{counts[key]}</span>
            </button>
          ))}
        </div>
        <input type="search" value={text} placeholder={t("backupPolicies.filter")} onChange={(event) => setText(event.target.value)} />
        {data.can_configure && (
          <Link to="/backups/policies/new" search={customerId ? { customer: customerId } : {}} className="ui-button ui-button-primary">{t("backupOverview.newPolicy")}</Link>
        )}
      </div>
      {rows.length === 0 ? (
        <div className="ui-empty">{data.items.length ? t("backupPolicies.noMatch") : t("backupOverview.policies.none")}</div>
      ) : (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead>
              <tr>{["name", "scope", "schedule", "devices", "state", "actions"].map((key) => <th key={key}><span className="ui-th">{t(`backupPolicies.columns.${key}`)}</span></th>)}</tr>
            </thead>
            <tbody>{rows.map((row) => <Row key={row.id} row={row} canConfigure={data.can_configure} inCustomer={Boolean(customerId)} />)}</tbody>
          </table>
        </div>
      )}
    </>
  );
}

function Row({ row, canConfigure, inCustomer }: { row: BackupPolicyRow; canConfigure: boolean; inCustomer: boolean }) {
  const { t } = useTranslation();
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");
  const toggle = usePolicyWrite<undefined, { enabled: boolean }>(`/${row.id}/toggle`);
  const remove = usePolicyWrite<{ confirm_name: string }, { deleted: boolean }>(`/${row.id}/delete`);
  const error = toggle.error ?? remove.error;
  return (
    <tr>
      <td>
        {canConfigure ? <Link to="/backups/policies/$policyId/edit" params={{ policyId: row.id }} className="ui-cell-title">{row.name}</Link> : <strong>{row.name}</strong>}
        {row.description && <small className="ui-muted ui-block">{row.description}</small>}
      </td>
      <td>{row.scope}{inCustomer && row.shared && <small className="ui-muted ui-block">{t("backupPolicies.shared")}</small>}</td>
      <td>{row.schedule}</td>
      <td>
        <strong>{row.devices}</strong>
        {row.devices > 0 && (
          <small className="ui-muted ui-block">
            {t("backupPolicies.coverage", { protected: row.protected, blocked: row.blocked })}
            {row.failed > 0 && <> · <span className="ui-text-danger">{t("backupPolicies.failed", { count: row.failed })}</span></>}
          </small>
        )}
      </td>
      <td><span className={row.enabled ? "ui-severity" : "ui-severity ui-severity-warning"}>{row.enabled ? t("backupOverview.policies.on") : t("backupOverview.policies.off")}</span></td>
      <td>
        {canConfigure && (
          <div className="ui-row-actions">
            <Link to="/backups/policies/$policyId/edit" params={{ policyId: row.id }} className="ui-button ui-button-ghost ui-button-small">{t("backupPolicies.edit")}</Link>
            <Link to="/backups/policies/$policyId/clone" params={{ policyId: row.id }} className="ui-button ui-button-ghost ui-button-small">{t("backupPolicies.clone")}</Link>
            <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={toggle.isPending} onClick={() => toggle.mutate(undefined)}>
              {row.enabled ? t("backupPolicies.disable") : t("backupPolicies.enable")}
            </button>
            {!confirming ? (
              <button type="button" className="ui-button ui-button-ghost ui-button-small ui-text-danger" onClick={() => setConfirming(true)}>{t("customerForm.delete")}</button>
            ) : (
              <form className="ui-inline-form" onSubmit={(event) => { event.preventDefault(); remove.mutate({ confirm_name: typed }); }}>
                <input value={typed} autoFocus placeholder={row.name} aria-label={t("backupPolicies.confirm", { name: row.name })} onChange={(event) => setTyped(event.target.value)} />
                <button type="submit" className="ui-button ui-button-danger ui-button-small" disabled={remove.isPending || typed.trim() !== row.name}>{t("customerForm.delete")}</button>
                <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={() => { setConfirming(false); setTyped(""); }}>{t("backupPolicies.cancel")}</button>
              </form>
            )}
          </div>
        )}
        {confirming && <small className="ui-muted ui-block">{t("backupPolicies.confirm", { name: row.name })}</small>}
        {error && <small className="ui-backup-error ui-block">{(error as Error).message}</small>}
      </td>
    </tr>
  );
}
