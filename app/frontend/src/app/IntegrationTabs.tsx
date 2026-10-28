import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

/** Configurable integrations. */
const INTEGRATION_PAGES: { key: string; route: string }[] = [
  { key: "uisp", route: "/admin/integrations/uisp" },
  { key: "nvd", route: "/admin/integrations/nvd" },
  { key: "genieacs", route: "/admin/integrations/genieacs" },
  { key: "zabbix", route: "/admin/integrations/zabbix" },
  { key: "cnmaestro", route: "/admin/integrations/cnmaestro" },
  { key: "ispbilling", route: "/admin/integrations/ispbilling" },
  { key: "bras", route: "/admin/integrations/bras" },
  { key: "probe", route: "/admin/integrations/probe" },
  { key: "threatLists", route: "/admin/integrations/threat-lists" },
  { key: "abuseipdb", route: "/admin/integrations/abuseipdb" },
  { key: "ripestat", route: "/admin/integrations/ripestat" },
];

export function IntegrationTabs() {
  const { t } = useTranslation();
  return (
    <nav className="ui-tabs ui-subtabs" aria-label={t("integrations.title")}>
      {INTEGRATION_PAGES.map((page) => (
        <Link key={page.key} to={page.route} className="ui-tab" activeProps={{ className: "ui-tab is-active", "aria-current": "page" }}>
          {t(`integrations.tabs.${page.key}`)}
        </Link>
      ))}
    </nav>
  );
}
