import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { formatDuration, formatRelative } from "../lib/format";

export interface PppoeSessionState {
  online: boolean;
  address: string | null;
  mac: string | null;
  caller_id: string | null;
  service: string | null;
  uptime_seconds: number | null;
  started_at: string | null;
  offline_since: string | null;
  last_seen_at: string | null;
}

interface CustomerPppoe {
  bras: boolean;
  last_sync_at: string | null;
  accounts: {
    username: string;
    profile: string | null;
    status: string | null;
    static_ip: string | null;
    session: PppoeSessionState | null;
    device: { id: string; name: string } | null;
    matched_by: string | null;
  }[];
}

/** PPPoE session of one account as read on the MikroTik BRAS: online since when (IP, MAC) or offline since when. */
export function PppoeSession({ session }: { session: PppoeSessionState | null | undefined }) {
  const { t, i18n } = useTranslation();
  if (!session) return <small className="ui-muted ui-block">{t("pppoe.never")}</small>;
  if (!session.online) {
    return (
      <small className="ui-block">
        <span className="ui-status ui-status-offline">{t("pppoe.offline")}</span>{" "}
        {session.offline_since && t("pppoe.offlineSince", { when: formatRelative(session.offline_since, i18n.language) })}
      </small>
    );
  }
  return (
    <small className="ui-block">
      <span className="ui-status ui-status-online">{t("pppoe.online")}</span>{" "}
      {[session.address, session.mac ?? session.caller_id, session.uptime_seconds != null && t("pppoe.uptime", { value: formatDuration(session.uptime_seconds, i18n.language) })]
        .filter(Boolean).join(" · ")}
    </small>
  );
}

/** Customer page: the customer's RADIUS accounts (ISPBilling) with their PPPoE session on the BRAS. */
export function CustomerPppoe({ customerId }: { customerId: string }) {
  const { t, i18n } = useTranslation();
  const query = useQuery({ queryKey: ["customer", customerId, "pppoe"], queryFn: () => api<CustomerPppoe>(`/api/ui/v1/customers/${customerId}/pppoe`), staleTime: 60_000 });
  const data = query.data;
  if (!data || !data.accounts.length) return null;
  return (
    <section className="ui-panel">
      <div className="ui-panel-header">
        <div>
          <h2>{t("pppoe.title")}</h2>
          <p className="ui-muted">
            {data.bras ? t("pppoe.help", { when: data.last_sync_at ? formatRelative(data.last_sync_at, i18n.language) : "—" }) : t("pppoe.noBras")}
          </p>
        </div>
      </div>
      <ul className="ui-list">
        {data.accounts.map((account) => (
          <li key={account.username}>
            <span>
              <strong className="ui-mono">{account.username}</strong>
              <small className="ui-muted ui-block">{[account.profile, account.status, account.static_ip].filter(Boolean).join(" · ")}</small>
              {data.bras && <PppoeSession session={account.session} />}
            </span>
            <span>
              {account.device ? (
                <Link to="/devices/$deviceId" params={{ deviceId: account.device.id }}>{account.device.name}</Link>
              ) : (
                <small className="ui-muted">{t("pppoe.noDevice")}</small>
              )}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
