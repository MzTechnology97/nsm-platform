import { Link } from "@tanstack/react-router";
import { Trans, useTranslation } from "react-i18next";

import { useAcsAdmin, useAcsTemplates } from "../api/admin";
import { ApiError } from "../api/client";
import { AdminTabs } from "../app/AdminTabs";
import { AcsSpeedtestSettings } from "../app/AcsSpeedtestSettings";
import { AcsSyslogAdminPanel } from "../app/AcsSyslog";
import { AcsAlertSettings } from "../app/AcsAlertSettings";
import { AcsFirmwareAutoSettings } from "../app/AcsFirmwareAutoSettings";
import { Loading } from "../app/States";
import { formatDateTime } from "../lib/format";

const STEPS = ["cpe", "network", "templates", "verify", "nsm"] as const;

export function AdminAcs() {
  const { t, i18n } = useTranslation();
  const query = useAcsAdmin();
  const install = useAcsTemplates();
  const data = query.data;
  const port = data ? (data.panel_url.match(/:(\d+)\/?$/)?.[1] ?? "7080") : "7080";
  const installError = install.error instanceof ApiError && install.error.code === "not_connected"
    ? t("acs.notConnected") : install.error ? (install.error as Error).message : null;
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("admin.title")}</h1>
          <p>{t("acs.subtitle")}</p>
        </div>
      </header>
      <AdminTabs />
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : (
        <>
          <div className="ui-metrics">
            <div className="ui-metric">
              <span>{t("acs.link")}</span><strong>{t(`acs.states.${data.state}`)}</strong>
              <small>{data.base_url ?? (data.internal_available ? t("acs.internalAvailable") : t("acs.enableHint"))}</small>
            </div>
            <div className="ui-metric"><span>{t("acs.cpeUrl")}</span><strong><code>{data.acs_url}</code></strong><small>{t("acs.cwmpPort")}</small></div>
            <div className="ui-metric"><span>{t("acs.panel")}</span><strong>{t("acs.port", { port })}</strong><small>{t("acs.panelAccess")}</small></div>
          </div>

          <section className="ui-panel ui-admin-section">
            <div className="ui-page-header-row">
              <h2>{t("acs.panelTitle")}</h2>
              <a className="ui-button" href={data.panel_url} target="_blank" rel="noopener noreferrer">{t("acs.openPanel")}</a>
            </div>
            <p className="ui-muted">{t("acs.panelHelp")}</p>
            {data.state === "unconfigured" && <p className="ui-muted">{t("acs.linkHint")} <Link to="/admin/integrations/genieacs">{t("acs.linkPage")}</Link></p>}
            <p className="ui-muted">{t("acs.migrationHint")} <Link to="/admin/acs/migration">{t("acs.migrationLink")}</Link></p>
          </section>

          <section className="ui-panel ui-admin-section" id="guide">
            <h2>{t("acs.guideTitle")}</h2>
            <p>{t("acs.guideIntro")}</p>
            <p className="ui-muted">{t("acs.guideHelp")}</p>
            <ol className="ui-acs-steps">
              {STEPS.map((step) => (
                <li key={step}>
                  <strong>{t(`acs.steps.${step}.title`)}</strong>{" "}
                  <Trans i18nKey={`acs.steps.${step}.text`} values={{ url: data.acs_url }} components={{ code: <code /> }} />
                </li>
              ))}
            </ol>
            <h3>{t("acs.readonlyTitle")}</h3>
            <ul className="ui-acs-steps">
              {(["reads", "actions", "never"] as const).map((key) => <li key={key}>{t(`acs.readonly.${key}`)}</li>)}
            </ul>
            <h3>{t("acs.securityTitle")}</h3>
            <ul className="ui-acs-steps">
              <li><Trans i18nKey="acs.security.auth" components={{ code: <code /> }} /></li>
              <li>{t("acs.security.network")}</li>
            </ul>
          </section>

          <AcsSpeedtestSettings />

          <AcsAlertSettings />

          <AcsFirmwareAutoSettings />

          <section className="ui-panel ui-admin-section" id="templates">
            <div className="ui-page-header-row">
              <h2>{t("acs.templatesTitle")}</h2>
              <button type="button" className="ui-button" disabled={install.isPending} onClick={() => install.mutate()}>{t("acs.install")}</button>
            </div>
            <p className="ui-muted">{t("acs.templatesHelp")}</p>
            {data.templates?.installed_at ? (
              <div className={`ui-alert${data.templates.current ? "" : " ui-alert-warning"}`}>
                {t(data.templates.current ? "acs.templatesCurrent" : "acs.templatesPending", { when: formatDateTime(data.templates.installed_at, i18n.language) })}
              </div>
            ) : (
              <p className="ui-muted">{t("acs.templatesAuto")}</p>
            )}
            {install.data && <div className="ui-alert">{t("acs.installed", { items: install.data.installed.join(", ") })}</div>}
            {installError && <div className="ui-alert ui-alert-warning">{installError}</div>}
            <dl className="ui-dl">
              {data.presets.map((preset) => (
                <div key={preset.name}>
                  <dt>{t("acs.preset")} <code>{preset.name}</code></dt>
                  <dd><Trans i18nKey="acs.presetText" values={{ condition: preset.precondition, provision: data.base_provision }} components={{ code: <code /> }} /></dd>
                </div>
              ))}
            </dl>
            <details>
              <summary>{t("acs.script", { name: data.base_provision })}</summary>
              <pre className="ui-code-block">{data.provision_script}</pre>
            </details>
          </section>
          <AcsSyslogAdminPanel />
        </>
      )}
    </section>
  );
}
