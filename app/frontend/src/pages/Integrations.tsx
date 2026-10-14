import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useIntegrations } from "../api/admin";
import { formatDateTime } from "../lib/format";

const TONE: Record<string, string> = {
  active: "online", error: "offline", disabled: "offline", stale: "offline", partial: "warning", pending: "warning", unconfigured: "unknown", none: "unknown",
};

function Card({ name, title, help, state, label, children, actions }: {
  name: string; title: string; help: string; state?: string; label?: string; children?: ReactNode; actions?: ReactNode;
}) {
  return (
    <section className="ui-panel ui-integration-card" data-integration={name}>
      <div className="ui-page-header-row">
        <h2>{title}</h2>
        {state && <span className={`ui-status ui-status-${TONE[state] ?? "unknown"}`}>{label}</span>}
      </div>
      <p className="ui-muted">{help}</p>
      {children}
      {actions && <div className="ui-row-actions">{actions}</div>}
    </section>
  );
}

function Facts({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="ui-dl">
      {items.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
    </dl>
  );
}

export function Integrations() {
  const { t, i18n } = useTranslation();
  const query = useIntegrations();
  const data = query.data;
  const when = (value: string | null) => (value ? formatDateTime(value, i18n.language) : t("adminUsers.never"));
  const state = (key: string) => t(`integrations.states.${key}`);
  const manage = (href: string, configured: boolean) =>
    data?.can_admin ? <a className="ui-button ui-button-ghost ui-button-small" href={href}>{configured ? t("integrations.manage") : t("integrations.configure")}</a> : null;

  const manageRoute = (to: "/admin/integrations/cnmaestro" | "/admin/integrations/zabbix" | "/admin/integrations/genieacs", configured: boolean) =>
    data?.can_admin ? <Link className="ui-button ui-button-ghost ui-button-small" to={to}>{configured ? t("integrations.manage") : t("integrations.configure")}</Link> : null;

  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("integrations.title")}</h1>
          <p>{t("integrations.subtitle")}</p>
        </div>
        <a className="ui-button ui-button-ghost" href="/integrations">{t("app.classic")}</a>
      </header>
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <div className="ui-empty">{t("app.loading")}</div>
      ) : (
        <div className="ui-integration-grid">
          <Card name="mikrotik-agent" title={t("integrations.agent.title")} help={t("integrations.agent.help")} state={data.agent.state}
            label={data.agent.state === "partial" ? t("integrations.agent.partial", { active: data.agent.total - data.agent.stale, total: data.agent.total }) : t(`integrations.agent.${data.agent.state}`)}
            actions={<Link className="ui-button ui-button-ghost ui-button-small" to="/agents">{t("integrations.agent.open")}</Link>}>
            <Facts items={[[t("integrations.agent.total"), data.agent.total], [t("integrations.agent.staleLabel"), data.agent.stale]]} />
          </Card>

          <Card name="uisp" title={t("integrations.uisp.title")} help={t("integrations.uisp.help")} state={data.uisp.state} label={state(data.uisp.state)}
            actions={manage("/admin/integrations/uisp", data.uisp.state !== "unconfigured")}>
            {data.uisp.state === "unconfigured" ? <p className="ui-muted">{t("integrations.uisp.setup")}</p> : (
              <Facts items={[
                [t("integrations.uisp.devices"), data.uisp.devices],
                [t("integrations.lastSync"), when(data.uisp.last_success_at)],
                ...(data.uisp.failures ? [[t("integrations.failures"), data.uisp.failures] as [string, ReactNode]] : []),
              ]} />
            )}
          </Card>

          <Card name="public-api" title={t("integrations.api.title")} help={t("integrations.api.help")} state={data.api.active_keys ? "active" : "unconfigured"}
            label={t("integrations.api.keys", { count: data.api.active_keys })}
            actions={data.can_admin ? <Link className="ui-button ui-button-ghost ui-button-small" to="/admin/api-keys">{t("integrations.api.manage")}</Link> : null} />

          <Card name="routeros" title={t("integrations.routeros.title")} help={t("integrations.routeros.help")}
            actions={<>
              <Link className="ui-button ui-button-ghost ui-button-small" to="/integrations/routeros">{t("integrations.routeros.open")}</Link>
              <Link className="ui-button ui-button-ghost ui-button-small" to="/firmware">{t("integrations.routeros.firmware")}</Link>
            </>} />

          <Card name="nvd" title={t("integrations.nvd.title")} help={t("integrations.nvd.help")} state={data.nvd.state} label={state(data.nvd.state)}
            actions={<>
              <Link className="ui-button ui-button-ghost ui-button-small" to="/vulnerabilities">{t("integrations.nvd.vulnerabilities")}</Link>
              {data.can_admin && <Link className="ui-button ui-button-ghost ui-button-small" to="/admin/integrations/nvd">{t("integrations.configure")}</Link>}
            </>}>
            <Facts items={[[t("integrations.nvd.advisories"), data.nvd.advisories], [t("integrations.nvd.updated"), when(data.nvd.last_success_at)]]} />
          </Card>

          <Card name="genieacs" title={t("integrations.genieacs.title")} help={t("integrations.genieacs.help")} state={data.genieacs.state} label={state(data.genieacs.state)}
            actions={manageRoute("/admin/integrations/genieacs", data.genieacs.state !== "unconfigured")}>
            <Facts items={[[t("integrations.genieacs.linked"), data.genieacs.linked], [t("integrations.genieacs.pending"), data.genieacs.pending]]} />
            <p className="ui-muted">{t("integrations.genieacs.limits")}</p>
          </Card>

          <Card name="cnmaestro" title={t("integrations.cnmaestro.title")} help={t("integrations.cnmaestro.help")} state={data.cnmaestro.state} label={state(data.cnmaestro.state)}
            actions={manageRoute("/admin/integrations/cnmaestro", data.cnmaestro.state !== "unconfigured")}>
            <Facts items={[[t("integrations.cnmaestro.cambium"), data.cnmaestro.cambium], [t("integrations.cnmaestro.linked"), data.cnmaestro.linked]]} />
          </Card>

          <Card name="zabbix" title={t("integrations.zabbix.title")} help={t("integrations.zabbix.help")} state={data.zabbix.state} label={state(data.zabbix.state)}
            actions={<>
              {data.zabbix.frontend && <a className="ui-button ui-button-ghost ui-button-small" href={data.zabbix.frontend} target="_blank" rel="noopener noreferrer">{t("integrations.zabbix.open")}</a>}
              {manageRoute("/admin/integrations/zabbix", data.zabbix.state !== "unconfigured")}
            </>}>
            <Facts items={[[t("integrations.zabbix.version"), data.zabbix.version ?? "—"], [t("integrations.zabbix.linked"), data.zabbix.linked]]} />
          </Card>
        </div>
      )}
    </section>
  );
}
