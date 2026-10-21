import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import { Fragment, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useNavCounts, type NavCounts } from "../api/navCounts";
import { useUnreadNotifications } from "../api/personal";
import { can, useSession, useUpdatePreferences } from "../api/session";
import i18n, { LANGUAGE_NAMES, LANGUAGES, pickLanguage, type Language } from "../i18n";
import { CommandPalette, isApple } from "./CommandPalette";
import { Icon } from "./Icon";
import { SECTIONS, type Section } from "./sections";

const LANGUAGE_COOKIE = "nsm-lang";

/** Factory branding colours, current and previous (app/app/branding_palette.py): they mean "not customised". */
const FACTORY_COLORS: Record<"primary" | "sidebar" | "highlight", string[]> = {
  primary: ["#1769d6", "#1f5f8b"],
  sidebar: ["#10192b", "#111827"],
  highlight: ["#f79009", "#f59e0b"],
};

/** Light or dark for the page, from the preference (``system`` follows the operating system). */
function useEffectiveTheme(preference: string): "light" | "dark" {
  const [systemDark, setSystemDark] = useState(() => window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false);
  useEffect(() => {
    const query = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!query) return;
    const listener = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    query.addEventListener("change", listener);
    return () => query.removeEventListener("change", listener);
  }, []);
  if (preference === "system") return systemDark ? "dark" : "light";
  return preference === "dark" ? "dark" : "light";
}

function readLanguageCookie(): string | null {
  const match = document.cookie.match(/(?:^|; )nsm-lang=([a-z]{2})/);
  return match ? match[1] : null;
}

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
  const [paletteOpen, setPaletteOpen] = useState(false);
  const preference = session.data?.preferences.theme ?? "light";
  const theme = useEffectiveTheme(preference);
  const navCounts = useNavCounts(Boolean(session.data)).data;

  useEffect(() => {
    if (!session.data) return;
    void i18n.changeLanguage(pickLanguage(session.data.preferences.language ?? readLanguageCookie())).then(() => {
      document.documentElement.lang = i18n.language;
      // The sign-in pages read the same cookie: the next login opens in this language.
      document.cookie = `${LANGUAGE_COOKIE}=${i18n.language}; path=/; max-age=31536000; samesite=lax`;
    });
  }, [session.data]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((value) => !value);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const branding = session.data?.branding;
  useEffect(() => {
    if (!branding) return;
    // CSSOM custom properties (allowed by the CSP, unlike inline style attributes).
    // The factory colours mean "not customised": the theme palette (light and dark) applies instead.
    const root = document.documentElement.style;
    const apply = (name: string, value: string, factory: string[]) => {
      if (value && !factory.includes(value.toLowerCase())) root.setProperty(name, value);
      else root.removeProperty(name);
    };
    apply("--brand-primary", branding.primary_color, FACTORY_COLORS.primary);
    apply("--brand-sidebar", branding.sidebar_color, FACTORY_COLORS.sidebar);
    apply("--brand-highlight", branding.highlight_color, FACTORY_COLORS.highlight);
  }, [branding]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("nsm-theme", preference);
    } catch {
      /* private mode */
    }
  }, [theme, preference]);

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
  const groups = [...new Set(visible.map((section) => section.group))];

  return (
    <div className={`ui-shell${collapsed ? " is-collapsed" : ""}${mobileOpen ? " is-mobile-open" : ""}`}>
      <aside className="ui-sidebar" aria-label="NSM">
        <div className="ui-brand">
          <span className="ui-brand-name" title={data.branding.portal_name}>
            {data.branding.has_logo ? (
              <img className="ui-brand-logo" src={`/branding/logo?v=${data.branding.logo_version}`} alt="" />
            ) : (
              <span className="ui-brand-mark" aria-hidden="true">{(data.branding.portal_name.trim()[0] ?? "N").toUpperCase()}</span>
            )}
            <span className="ui-brand-words">
              <span className="ui-brand-text">{data.branding.portal_name}</span>
              {data.branding.tagline && <span className="ui-brand-tagline">{data.branding.tagline}</span>}
            </span>
          </span>
          <button type="button" className="ui-icon-button ui-collapse" onClick={() => setCollapsed((value) => !value)} aria-label={t("nav.collapse")}>
            <Icon name="chevron-left" size={18} />
          </button>
        </div>
        <nav className="ui-nav">
          {groups.map((group) => (
            <Fragment key={group}>
              <span className="ui-nav-group">{t(`nav.groups.${group}`)}</span>
              {visible.filter((section) => section.group === group).map((section) => (
                <Link key={section.key} to={section.route} className="ui-nav-item" activeProps={{ className: "ui-nav-item is-active" }}
                  activeOptions={{ exact: section.route === "/" }} onClick={() => setMobileOpen(false)} title={t(`nav.${section.key}`)}>
                  <Icon name={section.icon} />
                  <span className="ui-nav-label">{t(`nav.${section.key}`)}</span>
                  <NavBadge badge={navBadge(section.key, navCounts)} />
                </Link>
              ))}
            </Fragment>
          ))}
        </nav>
        <div className="ui-sidebar-footer">{t("app.version", { version: data.version })}</div>
      </aside>
      <div className="ui-main">
        <header className="ui-topbar">
          <button type="button" className="ui-icon-button ui-mobile-menu" onClick={() => setMobileOpen(true)} aria-label={t("nav.open")}>
            <Icon name="menu" />
          </button>
          <Breadcrumbs sections={visible} />
          <button type="button" className="ui-top-search" onClick={() => setPaletteOpen(true)} aria-haspopup="dialog">
            <Icon name="search" size={16} />
            <span className="ui-top-search-label">{t("palette.trigger")}</span>
            <kbd className="ui-kbd ui-hide-mobile">{isApple ? "⌘K" : "Ctrl K"}</kbd>
          </button>
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
              title={`${t("user.theme")}: ${theme === "dark" ? t("user.light") : t("user.dark")}`}
            >
              <Icon name={theme === "dark" ? "sun" : "moon"} />
            </button>
            <Link to="/profile" className="ui-user-link" title={t("profile.title")} aria-label={data.user.display_name}>
              <span className="ui-user-avatar" aria-hidden="true">{initials(data.user.display_name)}</span>
              <span className="ui-user-name ui-hide-mobile">{data.user.display_name}</span>
            </Link>
            <form method="post" action="/logout" className="ui-inline-form">
              <input type="hidden" name="csrf" value={data.csrf_token} />
              <button type="submit" className="ui-button ui-button-ghost">{t("user.logout")}</button>
            </form>
          </div>
        </header>
        <main className="ui-content">
          <Outlet />
        </main>
      </div>
      {mobileOpen && <button type="button" className="ui-backdrop" aria-label={t("nav.collapse")} onClick={() => setMobileOpen(false)} />}
      <CommandPalette session={data} open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}

interface Badge {
  count: number;
  tone: "danger" | "warning" | "neutral";
  label: string;
}

/** Counter shown next to a sidebar item (none when the figure is missing or zero). */
function navBadge(key: string, counts: NavCounts | undefined): Badge | null {
  if (!counts) return null;
  const pick = (count: number | undefined, tone: Badge["tone"], label: string): Badge | null =>
    count ? { count, tone, label } : null;
  switch (key) {
    case "devices":
      return pick(counts.devices, "neutral", "devices");
    case "monitoring":
      return pick(counts.offline, "danger", "offline");
    case "vulnerabilities":
      return pick(counts.severe_cve_devices, "danger", "severe");
    case "firmware":
      return pick(counts.firmware_attention, "warning", "firmware");
    case "actionCenter":
      return pick(counts.open_issues, counts.critical_issues ? "danger" : "warning", "issues");
    default:
      return null;
  }
}

function NavBadge({ badge }: { badge: Badge | null }) {
  const { t } = useTranslation();
  if (!badge) return null;
  return (
    <>
      <span className={`ui-nav-badge${badge.tone === "neutral" ? "" : ` is-${badge.tone}`}`} aria-hidden="true">
        {badge.count > 999 ? "999+" : badge.count}
      </span>
      <span className="ui-visually-hidden">{`, ${t(`nav.badges.${badge.label}`, { count: badge.count })}`}</span>
    </>
  );
}

/** Up to two initials of the signed-in user, for the avatar circle of the top bar. */
function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const letters = words.length > 1 ? [words[0], words[words.length - 1]] : words;
  return letters.map((word) => word[0]).join("").toUpperCase() || "?";
}

/** Where the page sits: section group, then the section (a link back to it on the pages below it). */
function Breadcrumbs({ sections }: { sections: Section[] }) {
  const { t } = useTranslation();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const path = pathname.replace(/^\/app(?=\/|$)/, "") || "/";
  const matches = (section: Section) => {
    if (section.route === "/") return path === "/";
    const root = section.route.startsWith("/admin/") ? "/admin" : section.route;
    return path === root || path.startsWith(`${root}/`);
  };
  const section = sections.filter(matches).sort((a, b) => b.route.length - a.route.length)[0];
  if (!section) return <span className="ui-top-crumbs" />;
  const atRoot = path === section.route;
  return (
    <nav className="ui-top-crumbs" aria-label={t("nav.breadcrumb")}>
      <ol>
        <li className="ui-top-crumbs-group">{t(`nav.groups.${section.group}`)}</li>
        <li>
          {atRoot ? (
            <strong aria-current="page">{t(`nav.${section.key}`)}</strong>
          ) : (
            <Link to={section.route}>{t(`nav.${section.key}`)}</Link>
          )}
        </li>
      </ol>
    </nav>
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
