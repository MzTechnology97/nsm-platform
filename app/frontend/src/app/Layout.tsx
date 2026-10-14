import { Link, Outlet } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { can, useSession, useUpdatePreferences } from "../api/session";
import i18n, { LANGUAGE_NAMES, LANGUAGES, pickLanguage, type Language } from "../i18n";
import { Icon } from "./Icon";
import { SECTIONS } from "./sections";

const SIDEBAR_KEY = "nsm-ui-sidebar-collapsed";

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === "1";
  } catch {
    return false;
  }
}

export function Layout() {
  const { t } = useTranslation();
  const session = useSession();
  const update = useUpdatePreferences();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [mobileOpen, setMobileOpen] = useState(false);
  const theme = session.data?.preferences.theme ?? "light";

  useEffect(() => {
    if (!session.data) return;
    void i18n.changeLanguage(pickLanguage(session.data.preferences.language));
    document.documentElement.lang = i18n.language;
  }, [session.data]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("nsm-theme", theme);
    } catch {
      /* private mode */
    }
  }, [theme]);

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_KEY, collapsed ? "1" : "0");
    } catch {
      /* private mode */
    }
  }, [collapsed]);

  if (session.isPending) return <div className="ui-splash">{t("app.loading")}</div>;
  if (session.isError || !session.data) return <div className="ui-splash">{t("errors.session")}</div>;

  const data = session.data;
  const visible = SECTIONS.filter((section) => !section.permission || can(data, section.permission));

  return (
    <div className={`ui-shell${collapsed ? " is-collapsed" : ""}${mobileOpen ? " is-mobile-open" : ""}`}>
      <aside className="ui-sidebar" aria-label="NSM">
        <div className="ui-brand">
          <span className="ui-brand-name">{data.branding.portal_name}</span>
          <button type="button" className="ui-icon-button ui-collapse" onClick={() => setCollapsed((value) => !value)} aria-label={t("nav.collapse")}>
            <Icon name="chevron-left" size={18} />
          </button>
        </div>
        <nav className="ui-nav">
          {visible.map((section) =>
            section.route ? (
              <Link key={section.key} to={section.route} className="ui-nav-item" activeProps={{ className: "ui-nav-item is-active" }} onClick={() => setMobileOpen(false)}>
                <Icon name={section.icon} />
                <span className="ui-nav-label">{t(`nav.${section.key}`)}</span>
              </Link>
            ) : (
              <a key={section.key} href={section.classic} className="ui-nav-item is-classic" title={t("app.classic")}>
                <Icon name={section.icon} />
                <span className="ui-nav-label">{t(`nav.${section.key}`)}</span>
              </a>
            ),
          )}
        </nav>
        <div className="ui-sidebar-footer">{t("app.version", { version: data.version })}</div>
      </aside>
      <div className="ui-main">
        <header className="ui-topbar">
          <button type="button" className="ui-icon-button ui-mobile-menu" onClick={() => setMobileOpen(true)} aria-label={t("nav.open")}>
            <Icon name="menu" />
          </button>
          <span className="ui-preview-badge">{t("app.preview")}</span>
          <div className="ui-topbar-actions">
            <label className="ui-select-label">
              <span className="ui-visually-hidden">{t("user.language")}</span>
              <select
                value={pickLanguage(data.preferences.language)}
                onChange={(event) => update.mutate({ language: event.target.value as Language })}
                aria-label={t("user.language")}
              >
                {LANGUAGES.map((code) => (
                  <option key={code} value={code}>
                    {LANGUAGE_NAMES[code]}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="ui-icon-button"
              onClick={() => update.mutate({ theme: theme === "dark" ? "light" : "dark" })}
              aria-label={`${t("user.theme")}: ${theme === "dark" ? t("user.light") : t("user.dark")}`}
            >
              <Icon name={theme === "dark" ? "sun" : "moon"} />
            </button>
            <a className="ui-button ui-button-ghost ui-hide-mobile" href="/">
              {t("app.classic")}
            </a>
            <form method="post" action="/logout" className="ui-inline-form">
              <input type="hidden" name="csrf" value={data.csrf_token} />
              <button type="submit" className="ui-button ui-button-ghost">
                <span className="ui-hide-mobile">{data.user.display_name} · </span>
                {t("user.logout")}
              </button>
            </form>
          </div>
        </header>
        <main className="ui-content">
          <Outlet />
        </main>
      </div>
      {mobileOpen && <button type="button" className="ui-backdrop" aria-label={t("nav.collapse")} onClick={() => setMobileOpen(false)} />}
    </div>
  );
}
