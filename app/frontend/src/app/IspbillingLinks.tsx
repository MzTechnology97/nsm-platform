import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";

export interface IspbillingLinkState {
  state: "linked" | "site" | "conflict" | "not_imported";
  customer_id: string | null;
  site_id: string | null;
  customer?: string | null;
  site?: string | null;
  dismissed: boolean;
}

interface LinkRow extends IspbillingLinkState {
  account: string;
  username: string;
  matched_by: string | null;
  device: { id: string; name: string; customer: string | null; site: string | null };
}

interface LinksData {
  counts: Record<"linked" | "site" | "conflict" | "not_imported" | "dismissed", number>;
  rows: LinkRow[];
  can_apply: boolean;
}

const devicePath = (id: string) => `/api/ui/v1/devices/${encodeURIComponent(id)}/ispbilling`;

/** Apply or dismiss one proposal for a device; a device of another customer needs an explicit second click. */
export function useIspbillingLinkActions(deviceId: string) {
  const client = useQueryClient();
  const done = () => {
    void client.invalidateQueries({ queryKey: ["device", deviceId] });
    void client.invalidateQueries({ queryKey: ["ispbilling", "links"] });
  };
  const apply = useMutation({
    mutationFn: (body: { account: string; reassign?: boolean }) => api(`${devicePath(deviceId)}/link`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: done,
  });
  const dismiss = useMutation({
    mutationFn: (account: string) => api(`${devicePath(deviceId)}/dismiss`, { method: "POST", body: JSON.stringify({ account }) }),
    onSuccess: done,
  });
  return { apply, dismiss };
}

/** One account's proposal on the device page: set the site, or reassign the device after a second confirmation. */
export function IspbillingLinkProposal({ deviceId, account, link }: { deviceId: string; account: string; link: IspbillingLinkState }) {
  const { t } = useTranslation();
  const { apply, dismiss } = useIspbillingLinkActions(deviceId);
  const [confirming, setConfirming] = useState(false);
  if (link.dismissed || link.state === "linked") return null;
  if (link.state === "not_imported") {
    return <small className="ui-muted ui-block">{t("ispbillingLinks.notImported")} <Link to="/integrations/ispbilling/import">{t("ispbillingLinks.import")}</Link></small>;
  }
  const error = (apply.error || dismiss.error) as Error | null;
  return (
    <div className="ui-block">
      <small className={link.state === "conflict" ? "ui-key-hint ui-block" : "ui-muted ui-block"}>
        {link.state === "site" ? t("ispbillingLinks.proposeSite", { site: link.site ?? "—" }) : t("ispbillingLinks.conflict", { customer: link.customer ?? "—" })}
      </small>
      <div className="ui-row-actions">
        {link.state === "site" ? (
          <button type="button" className="ui-button ui-button-small" disabled={apply.isPending} onClick={() => apply.mutate({ account })}>{t("ispbillingLinks.applySite")}</button>
        ) : confirming ? (
          <>
            <span>{t("ispbillingLinks.reassignConfirm", { customer: link.customer ?? "—" })}</span>
            <button type="button" className="ui-button ui-button-danger ui-button-small" disabled={apply.isPending}
              onClick={() => apply.mutate({ account, reassign: true })}>{t("ispbillingLinks.reassign")}</button>
            <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={() => setConfirming(false)}>{t("customerForm.cancel")}</button>
          </>
        ) : (
          <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={() => setConfirming(true)}>{t("ispbillingLinks.reassignAsk")}</button>
        )}
        <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={dismiss.isPending} onClick={() => dismiss.mutate(account)}>{t("ispbillingLinks.dismiss")}</button>
      </div>
      {error && <small className="ui-backup-error ui-block">{error.message}</small>}
    </div>
  );
}

/** Proposals and conflicts over all matched ISPBilling accounts (Integrazioni → ISPBilling). */
export function IspbillingLinks() {
  const { t } = useTranslation();
  const query = useQuery({ queryKey: ["ispbilling", "links"], queryFn: () => api<LinksData>("/api/ui/v1/integrations/ispbilling/links") });
  const data = query.data;
  if (!data) return null;
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("ispbillingLinks.title")}</h2>
      <p className="ui-muted">{t("ispbillingLinks.help")}</p>
      <div className="ui-metrics">
        {(["site", "conflict", "not_imported", "linked"] as const).map((key) => (
          <div key={key} className="ui-metric"><span>{t(`ispbillingLinks.counts.${key}`)}</span><strong>{data.counts[key]}</strong></div>
        ))}
      </div>
      {data.rows.length === 0 ? (
        <p className="ui-muted">{t("ispbillingLinks.empty")}</p>
      ) : (
        <div className="ui-table-scroll">
          <table className="ui-stats-table">
            <thead>
              <tr><th>{t("ispbillingLinks.device")}</th><th>{t("ispbillingLinks.account")}</th><th>{t("ispbillingLinks.now")}</th><th>{t("ispbillingLinks.ispbilling")}</th><th /></tr>
            </thead>
            <tbody>
              {data.rows.map((row) => (
                <tr key={row.account}>
                  <td><Link to="/devices/$deviceId" params={{ deviceId: row.device.id }}>{row.device.name}</Link></td>
                  <td className="ui-mono">{row.username}<small className="ui-muted ui-block">{t(`device.ispbilling.matched.${row.matched_by ?? "pppoe"}`)}</small></td>
                  <td>{row.device.customer ?? "—"}<small className="ui-muted ui-block">{row.device.site ?? "—"}</small></td>
                  <td>{row.customer ?? "—"}<small className="ui-muted ui-block">{row.site ?? "—"}</small></td>
                  <td>{data.can_apply && <RowAction row={row} />}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function RowAction({ row }: { row: LinkRow }) {
  const { t } = useTranslation();
  const { apply } = useIspbillingLinkActions(row.device.id);
  if (row.state === "conflict") {
    return <Link to="/devices/$deviceId" params={{ deviceId: row.device.id }} className="ui-button ui-button-ghost ui-button-small">{t("ispbillingLinks.check")}</Link>;
  }
  return (
    <>
      <button type="button" className="ui-button ui-button-small" disabled={apply.isPending} onClick={() => apply.mutate({ account: row.account })}>{t("ispbillingLinks.applySite")}</button>
      {apply.error && <small className="ui-backup-error ui-block">{(apply.error as Error).message}</small>}
    </>
  );
}
