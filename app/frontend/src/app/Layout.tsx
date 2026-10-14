import { Link, Outlet, useNavigate } from "@tanstack/react-router";
import { type FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useUnreadNotifications } from "../api/personal";
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

  const branding = session.data?.branding;
  useEffect(() => {
    if (!branding) return;
    // CSSOM custom properties (allowed by the CSP, unlike inline style attributes), as the classic pages do.
    const root = document.documentElement.style;
    root.setProperty("--brand-primary", branding.primary_color);
    root.setProperty("--brand-sidebar", branding.sidebar_color);
    root.setProperty("--brand-highlight", branding.highlight_color);
  }, [branding]);

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
          <span className="ui-brand-name">
            {data.branding.has_logo && <img className="ui-brand-logo" src={`/branding/logo?v=${data.branding.logo_version}`} alt="" />}
            {data.branding.portal_name}
          </span>
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
          <TopSearch />
          <div className="ui-topbar-actions">
            <NotificationBell />
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

/** Global search box of the top bar: opens the search page with the term in the address. */
function TopSearch() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [term, setTerm] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const q = term.trim();
    if (!q) return;
    setTerm("");
    void navigate({ to: "/search", search: { q } });
  };
  return (
    <form className="ui-top-search" role="search" onSubmit={submit}>
      <Icon name="search" size={16} />
      <input type="search" value={term} onChange={(event) => setTerm(event.target.value)} placeholder={t("search.placeholder")}
        aria-label={t("search.title")} autoComplete="off" />
    </form>
  );
}

/** Bell with the unread count of the signed-in user, refreshed every minute. */
function NotificationBell() {
  const { t } = useTranslation();
  const unread = useUnreadNotifications().data?.unread ?? 0;
  return (
    <Link to="/notifications" className="ui-icon-button ui-bell" aria-label={t("notifications.bell", { count: unread })} title={t("notifications.bell", { count: unread })}>
      <Icon name="bell" />
      {unread > 0 && <span className="ui-bell-count">{unread > 99 ? "99+" : unread}</span>}
    </Link>
  );
}
