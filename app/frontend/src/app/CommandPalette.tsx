import { useNavigate } from "@tanstack/react-router";
import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useGlobalSearch } from "../api/personal";
import { can, type Session } from "../api/session";
import { useDebounced } from "../lib/hooks";
import { BrandIcon, type BrandRef } from "./BrandIcon";
import { Icon } from "./Icon";
import { SECTIONS } from "./sections";

const RECENT_KEY = "nsm-palette-recent";
const RECENT_MAX = 6;

type Kind = "page" | "device" | "customer" | "site" | "all";

/** Where an entry leads; resolved by ``go`` so every route stays type-checked. */
type Target =
  | { kind: "page"; route: string }
  | { kind: "device"; id: string }
  | { kind: "customer"; id: string }
  | { kind: "site"; customerId: string }
  | { kind: "all"; q: string };

export interface Entry {
  key: string;
  kind: Kind;
  label: string;
  detail?: string;
  icon?: string;
  brand?: BrandRef;
  status?: string;
  target: Target;
}

export const fold = (value: string) => value.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();

export function readRecent(): Entry[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
    return Array.isArray(parsed) ? (parsed as Entry[]).slice(0, RECENT_MAX) : [];
  } catch {
    return [];
  }
}

export function remember(entry: Entry) {
  if (entry.kind === "page" || entry.kind === "all") return;
  try {
    const rest = readRecent().filter((item) => item.key !== entry.key);
    localStorage.setItem(RECENT_KEY, JSON.stringify([entry, ...rest].slice(0, RECENT_MAX)));
  } catch {
    /* private mode */
  }
}

/** Ctrl+K / ⌘K: jump to a page, device, customer or site from anywhere; the top bar opens it too. */
export function CommandPalette({ session, open, onClose }: { session: Session; open: boolean; onClose: () => void }) {
  if (!open) return null;
  return <Palette session={session} onClose={onClose} />;
}

function Palette({ session, onClose }: { session: Session; onClose: () => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [text, setText] = useState("");
  const [active, setActive] = useState(0);
  const q = useDebounced(text.trim(), 200);
  const search = useGlobalSearch(q.length >= 2 ? q : "");
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);

  useEffect(() => input.current?.focus(), []);

  const pages = useMemo<Entry[]>(() => {
    const visible = SECTIONS.filter((section) => !section.permission || can(session, section.permission));
    const extra = [
      { key: "profile", icon: "admin", route: "/profile", label: t("profile.title") },
      { key: "notifications", icon: "bell", route: "/notifications", label: t("notifications.title") },
    ];
    return [
      ...visible.map((section) => ({ key: `page:${section.key}`, kind: "page" as const, label: t(`nav.${section.key}`),
        detail: t(`nav.groups.${section.group}`), icon: section.icon, target: { kind: "page" as const, route: section.route } })),
      ...extra.map((item) => ({ key: `page:${item.key}`, kind: "page" as const, label: item.label, icon: item.icon, target: { kind: "page" as const, route: item.route } })),
    ];
  }, [session, t]);

  const groups = useMemo(() => {
    const term = fold(text.trim());
    if (!term) {
      const recent = readRecent();
      return [
        ...(recent.length ? [{ title: t("palette.recent"), items: recent }] : []),
        { title: t("palette.pages"), items: pages },
      ];
    }
    const result: { title: string; items: Entry[] }[] = [];
    const matchedPages = pages.filter((page) => fold(page.label).includes(term) || fold(page.detail ?? "").includes(term));
    if (matchedPages.length) result.push({ title: t("palette.pages"), items: matchedPages });
    const data = q === text.trim() && q.length >= 2 ? search.data : undefined;
    if (data) {
      const devices = data.devices.slice(0, 8).map<Entry>((device) => ({
        key: `device:${device.id}`, kind: "device", label: device.name, brand: device.brand, status: device.status,
        detail: [device.customer?.name, device.management_ip, device.model].filter(Boolean).join(" · "), target: { kind: "device", id: device.id },
      }));
      const customers = data.customers.slice(0, 5).map<Entry>((customer) => ({
        key: `customer:${customer.id}`, kind: "customer", label: customer.name, detail: customer.code ?? undefined, icon: "customers",
        target: { kind: "customer", id: customer.id },
      }));
      const sites = data.sites.filter((site) => site.customer).slice(0, 5).map<Entry>((site) => ({
        key: `site:${site.id}`, kind: "site", label: site.name, detail: [site.customer?.name, site.address].filter(Boolean).join(" · "), icon: "customers",
        target: { kind: "site", customerId: site.customer!.id },
      }));
      if (devices.length) result.push({ title: t("palette.devices"), items: devices });
      if (customers.length) result.push({ title: t("palette.customers"), items: customers });
      if (sites.length) result.push({ title: t("palette.sites"), items: sites });
    }
    result.push({ title: "", items: [{ key: "all", kind: "all", label: t("palette.allResults", { term: text.trim() }), icon: "search", target: { kind: "all", q: text.trim() } }] });
    return result;
  }, [text, q, search.data, pages, t]);

  const flat = useMemo(() => groups.flatMap((group) => group.items), [groups]);
  useEffect(() => setActive(0), [text]);
  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const go = (entry: Entry) => {
    remember(entry);
    onClose();
    const target = entry.target;
    if (target.kind === "page") void navigate({ to: target.route });
    else if (target.kind === "device") void navigate({ to: "/devices/$deviceId", params: { deviceId: target.id } });
    else if (target.kind === "customer") void navigate({ to: "/customers/$customerId", params: { customerId: target.id } });
    else if (target.kind === "site") void navigate({ to: "/customers/$customerId/sites", params: { customerId: target.customerId } });
    else void navigate({ to: "/search", search: { q: target.q } });
  };

  const onKey = (event: KeyboardEvent) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((value) => Math.min(flat.length - 1, value + 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((value) => Math.max(0, value - 1));
    } else if (event.key === "Enter" && flat[active]) {
      event.preventDefault();
      go(flat[active]);
    } else if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    }
  };

  let index = -1;
  const searching = q.length >= 2 && search.isFetching;
  return (
    <div className="ui-palette-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="ui-palette" role="dialog" aria-modal="true" aria-label={t("palette.title")}>
        <div className="ui-palette-input">
          <Icon name="search" size={18} />
          <input ref={input} value={text} onChange={(event) => setText(event.target.value)} onKeyDown={onKey} placeholder={t("palette.placeholder")}
            role="combobox" aria-expanded="true" aria-controls="ui-palette-list" aria-activedescendant={flat[active] ? `palette-${active}` : undefined}
            aria-autocomplete="list" autoComplete="off" spellCheck={false} />
          {searching && <span className="ui-palette-spinner" aria-hidden="true" />}
          <kbd className="ui-kbd">Esc</kbd>
        </div>
        <ul ref={list} id="ui-palette-list" className="ui-palette-list" role="listbox" aria-label={t("palette.title")}>
          {groups.map((group) => (
            <li key={group.title || "end"} role="presentation">
              {group.title && <div className="ui-palette-group">{group.title}</div>}
              <ul role="presentation">
                {group.items.map((entry) => {
                  index += 1;
                  const current = index;
                  return (
                    <li key={entry.key} id={`palette-${current}`} data-index={current} role="option" aria-selected={current === active}
                      className={`ui-palette-item${current === active ? " is-active" : ""}`}
                      onMouseMove={() => setActive(current)} onClick={() => go(entry)}>
                      <span className="ui-palette-icon">
                        {entry.brand ? <BrandIcon brand={entry.brand} size={18} small /> : <Icon name={entry.icon ?? "search"} size={18} />}
                      </span>
                      <span className="ui-palette-text">
                        <strong>{entry.label}</strong>
                        {entry.detail && <small>{entry.detail}</small>}
                      </span>
                      {entry.status && <span className={`ui-status ui-status-${entry.status}`} aria-hidden="true" />}
                      <span className="ui-palette-kind">{entry.kind === "all" ? "↵" : t(`palette.kinds.${entry.kind}`)}</span>
                    </li>
                  );
                })}
              </ul>
            </li>
          ))}
          {q.length >= 2 && search.data && !searching && groups.length === 1 && (
            <li className="ui-palette-empty" role="presentation">{t("palette.noMatches")}</li>
          )}
        </ul>
        <div className="ui-palette-footer">
          <span><kbd className="ui-kbd">↑</kbd><kbd className="ui-kbd">↓</kbd> {t("palette.move")}</span>
          <span><kbd className="ui-kbd">↵</kbd> {t("palette.open")}</span>
          <span><kbd className="ui-kbd">Esc</kbd> {t("palette.close")}</span>
        </div>
      </div>
    </div>
  );
}

/** True on Apple keyboards, where the shortcut is ⌘K instead of Ctrl+K. */
export const isApple = typeof navigator !== "undefined" && /Mac|iPhone|iPad/i.test(navigator.platform || navigator.userAgent);
