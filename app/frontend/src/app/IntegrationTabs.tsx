import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

/** Configurable integrations: ``route`` once migrated to React, otherwise the classic page. */
const INTEGRATION_PAGES: { key: string; classic: string; route?: string }[] = [
  { key: "uisp", classic: "/admin/integrations/uisp" },
  { key: "nvd", classic: "/admin/integrations/nvd", route: "/admin/integrations/nvd" },
  { key: "genieacs", classic: "/admin/integrations/genieacs" },
  { key: "zabbix", classic: "/admin/integrations/zabbix" },
  { key: "cnmaestro", classic: "/admin/integrations/cnmaestro" },
];

export function IntegrationTabs() {
  const { t } = useTranslation();
  return (
    <nav className="ui-tabs ui-subtabs" aria-label={t("integrations.title")}>
      {INTEGRATION_PAGES.map((page) =>
        page.route ? (
          <Link key={page.key} to={page.route} className="ui-tab" activeProps={{ className: "ui-tab is-active", "aria-current": "page" }}>
            {t(`integrations.tabs.${page.key}`)}
          </Link>
        ) : (
          <a key={page.key} href={page.classic} className="ui-tab">{t(`integrations.tabs.${page.key}`)}</a>
        ),
      )}
    </nav>
  );
}
