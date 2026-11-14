import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../api/client";
import { useDeviceFacets } from "../api/devices";
import { type AclRule, AclRuleEditor } from "../app/AclRuleEditor";
import { SearchSelect, customerLabel } from "../app/SearchSelect";
import { Loading } from "../app/States";
import { UispFirewallBulkPanel } from "../app/UispFirewallBulk";

interface CommonAcl {
  services: string[];
  all_services: string[];
  rules: AclRule[];
  saved: boolean;
  networks: string[];
  max_rules: number;
  can_edit: boolean;
}

const PATH = "/api/ui/v1/security/acl";

function message(error: unknown) {
  return error instanceof ApiError ? error.message : (error as Error | null)?.message;
}

/** Security → ACL rules: the airOS firewall rules NSM writes on the UISP CPEs, and their nightly application. */
export function AclRules() {
  const { t } = useTranslation();
  const search = useSearch({ from: "/acl" });
  const navigate = useNavigate({ from: "/acl" });
  const facets = useDeviceFacets();
  const query = useQuery({ queryKey: ["security", "acl"], queryFn: () => api<CommonAcl>(PATH) });
  const customer = (
    <SearchSelect
      value={search.customer ?? ""}
      options={(facets.data?.customers ?? []).map((row) => ({ value: row.id, label: customerLabel(row) }))}
      onChange={(value) => void navigate({ search: { customer: value || undefined }, replace: true })}
      emptyLabel={t("devices.allCustomers")}
      ariaLabel={t("devices.customer")}
    />
  );
  return (
    <section className="ui-page">
      <header className="ui-page-header">
        <h1>{t("acl.title")}</h1>
        <p>{t("acl.subtitle")}</p>
      </header>
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{message(query.error)}</div>
      ) : !query.data ? (
        <Loading />
      ) : (
        <CommonRules data={query.data} />
      )}
      <UispFirewallBulkPanel customer={search.customer} toolbar={customer} />
    </section>
  );
}

function CommonRules({ data }: { data: CommonAcl }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [services, setServices] = useState(data.services);
  const [rules, setRules] = useState<AclRule[]>(data.rules);
  const save = useMutation({
    mutationFn: () => api<CommonAcl>(PATH, { method: "PUT", body: JSON.stringify({ services, rules }) }),
    onSuccess: (saved) => {
      setServices(saved.services);
      setRules(saved.rules);
      void client.invalidateQueries({ queryKey: ["security"] });
    },
  });
  const dirty = JSON.stringify({ services, rules }) !== JSON.stringify({ services: data.services, rules: data.rules });
  const toggle = (service: string) =>
    setServices(services.includes(service) ? services.filter((s) => s !== service) : data.all_services.filter((s) => s === service || services.includes(s)));
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("acl.common.title")}</h2>
      <p className="ui-muted">{t("acl.common.hint")}</p>
      <form className="ui-admin-stack" onSubmit={submit} autoComplete="off">
        <fieldset className="ui-row-actions">
          <legend>{t("uispFirewall.services")}</legend>
          {data.all_services.map((service) => (
            <label key={service}>
              <input type="checkbox" checked={services.includes(service)} disabled={!data.can_edit || (services.length === 1 && services.includes(service))}
                onChange={() => toggle(service)} />{" "}
              {t(`uispFirewall.service.${service}`)}
            </label>
          ))}
        </fieldset>
        <p>
          <strong>{t("acl.common.networks")}</strong>{" "}
          <span className="ui-mono">{data.networks.length ? data.networks.join(", ") : t("acl.common.noNetworks")}</span>
          {" · "}
          <Link to="/admin/management-access">{t("acl.common.manageNetworks")}</Link>
        </p>
        <h3>{t("acl.rules.title")}</h3>
        <p className="ui-muted">{t("acl.rules.hint")}</p>
        <AclRuleEditor rules={rules} onChange={setRules} max={data.max_rules} readOnly={!data.can_edit} />
        {data.can_edit && (
          <div className="ui-row-actions">
            <button type="submit" className="ui-button" disabled={!dirty || save.isPending}>{t("acl.save")}</button>
            {save.isSuccess && !dirty && <small className="ui-live">{t("acl.saved")}</small>}
            {save.error && <small className="ui-backup-error">{message(save.error)}</small>}
          </div>
        )}
      </form>
    </section>
  );
}
