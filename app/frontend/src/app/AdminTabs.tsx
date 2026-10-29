import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

/** Administration pages. */
const ADMIN_PAGES: { key: string; route: string }[] = [
  { key: "users", route: "/admin/users" },
  { key: "apiKeys", route: "/admin/api-keys" },
  { key: "integrations", route: "/admin/integrations/uisp" },
  { key: "branding", route: "/admin/branding" },
  { key: "demo", route: "/admin/demo" },
  { key: "notifications", route: "/admin/notifications" },
  { key: "syslog", route: "/admin/syslog" },
  { key: "managementAccess", route: "/admin/management-access" },
  { key: "acs", route: "/admin/acs" },
  { key: "system", route: "/admin/system" },
  { key: "serverMigration", route: "/admin/server-migration" },
];

export function AdminTabs() {
  const { t } = useTranslation();
  return (
    <nav className="ui-tabs" aria-label={t("admin.title")}>
      {ADMIN_PAGES.map((page) => (
        <Link key={page.key} to={page.route} className="ui-tab" activeProps={{ className: "ui-tab is-active", "aria-current": "page" }}>
          {t(`admin.tabs.${page.key}`)}
        </Link>
      ))}
    </nav>
  );
}
