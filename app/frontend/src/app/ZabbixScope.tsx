import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../api/client";

type Outcome = "pushed" | "linked" | "no_ip" | "factory_ip" | "pending";
interface PushResult { status: string; error: string | null; outcome?: Outcome }
interface CustomerZabbix { configured: boolean; can_manage: boolean; scope?: string; included?: boolean; devices?: number; in_zabbix?: number; selected?: number; no_ip?: number }

function message(error: unknown) {
  return error instanceof ApiError ? error.message : (error as Error | null)?.message;
}

/** *Aggiungi a Zabbix* / *Rimuovi da Zabbix* of one device: pushed at once, the result is said right away. */
export function DeviceZabbixButton({ deviceId, inZabbix }: { deviceId: string; inZabbix: boolean }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const action = useMutation({
    mutationFn: (add: boolean) => api<PushResult>(`/api/ui/v1/devices/${encodeURIComponent(deviceId)}/zabbix/${add ? "add" : "remove"}`, { method: "POST", body: "{}" }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["device", deviceId] }),
  });
  const result = action.data;
  return (
    <>
      <button type="button" className="ui-link-button" disabled={action.isPending} onClick={() => action.mutate(!inZabbix)}>
        {action.isPending ? t("zabbixScope.working") : inZabbix ? t("zabbixScope.removeDevice") : t("zabbixScope.addDevice")}
      </button>
      {result?.outcome && <small className="ui-muted ui-block">{t(`zabbixScope.outcome.${result.outcome}`)}</small>}
      {result?.error && <small className="ui-text-danger ui-block">{result.error}</small>}
      {action.error && <small className="ui-text-danger ui-block">{message(action.error)}</small>}
    </>
  );
}

/** Customer header: every device of the customer in (or out of) Zabbix. Hidden when Zabbix is not configured. */
export function CustomerZabbixButton({ customerId }: { customerId: string }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["customer", customerId, "zabbix"], queryFn: () => api<CustomerZabbix>(`/api/ui/v1/customers/${encodeURIComponent(customerId)}/zabbix`) });
  const action = useMutation({
    mutationFn: (add: boolean) => api<CustomerZabbix & PushResult>(`/api/ui/v1/customers/${encodeURIComponent(customerId)}/zabbix/${add ? "add" : "remove"}`, { method: "POST", body: "{}" }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["customer", customerId] });
      void client.invalidateQueries({ queryKey: ["device"] });
    },
  });
  const data = query.data;
  if (!data?.configured || !data.can_manage) return null;
  const included = Boolean(data.included);
  const summary = t("zabbixScope.customerSummary", { inZabbix: data.in_zabbix ?? 0, devices: data.devices ?? 0 });
  const label = action.isPending ? t("zabbixScope.working") : included ? t("zabbixScope.removeCustomer") : t("zabbixScope.addCustomer");
  const confirmText = included ? t("zabbixScope.confirmRemove", { count: data.devices ?? 0 }) : t("zabbixScope.confirmAdd", { count: data.devices ?? 0 });
  return (
    <span className="ui-zabbix-scope">
      <button type="button" className="ui-button ui-button-ghost" title={summary} disabled={action.isPending || !data.devices}
        onClick={() => { if (window.confirm(confirmText)) action.mutate(!included); }}>
        {label}
      </button>
      {action.data && (
        <small className={action.data.error ? "ui-text-danger ui-block" : "ui-muted ui-block"}>
          {action.data.error ?? t("zabbixScope.customerDone", { inZabbix: action.data.in_zabbix ?? 0, devices: action.data.devices ?? 0, noIp: action.data.no_ip ?? 0 })}
        </small>
      )}
      {action.error && <small className="ui-text-danger ui-block">{message(action.error)}</small>}
    </span>
  );
}
