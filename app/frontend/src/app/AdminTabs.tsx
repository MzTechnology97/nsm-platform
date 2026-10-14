import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

/** Administration pages: ``route`` once migrated to React, otherwise the classic page. */
const ADMIN_PAGES: { key: string; classic: string; route?: string }[] = [
  { key: "users", classic: "/admin/users", route: "/admin/users" },
  { key: "apiKeys", classic: "/admin/api-keys", route: "/admin/api-keys" },
  { key: "integrations", classic: "/admin/integrations/uisp" },
  { key: "branding", classic: "/admin/branding" },
  { key: "demo", classic: "/admin/demo" },
  { key: "notifications", classic: "/admin/notifications" },
  { key: "syslog", classic: "/admin/syslog" },
  { key: "acs", classic: "/admin/acs" },
  { key: "system", classic: "/admin/system" },
];

export function AdminTabs() {
  const { t } = useTranslation();
  return (
    <nav className="ui-tabs" aria-label={t("admin.title")}>
      {ADMIN_PAGES.map((page) =>
        page.route ? (
          <Link key={page.key} to={page.route} className="ui-tab" activeProps={{ className: "ui-tab is-active", "aria-current": "page" }}>
            {t(`admin.tabs.${page.key}`)}
          </Link>
        ) : (
          <a key={page.key} href={page.classic} className="ui-tab">{t(`admin.tabs.${page.key}`)}</a>
        ),
      )}
    </nav>
  );
}
