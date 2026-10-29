import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { type NotificationItem, useArchiveNotifications, useMarkNotificationRead, useNotifications, useUnreadNotifications } from "../api/personal";
import { formatDateTime, formatRelative } from "../lib/format";
import { BrandIcon } from "./BrandIcon";
import { Icon } from "./Icon";

const SHOWN = 15;

/**
 * Bell with a drop-down of the latest notifications, over the current page (like a social network): the operator reads,
 * marks and archives them without leaving the customer or device they are working on. Links open in a new browser tab;
 * only "Vedi tutte" goes to the notification page.
 */
export function NotificationMenu() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const unread = useUnreadNotifications().data?.unread ?? 0;
  const box = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return undefined;
    const outside = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const label = t("notifications.bell", { count: unread });
  return (
    <div className="ui-notify" ref={box}>
      <button ref={button} type="button" className="ui-icon-button ui-bell" aria-label={label} title={label} aria-haspopup="dialog" aria-expanded={open}
        onClick={() => setOpen((value) => !value)}>
        <Icon name="bell" />
        {unread > 0 && <span className="ui-bell-count">{unread > 99 ? "99+" : unread}</span>}
      </button>
      {open && <Panel unread={unread} onClose={() => setOpen(false)} />}
    </div>
  );
}

function Panel({ unread, onClose }: { unread: number; onClose: () => void }) {
  const { t } = useTranslation();
  const [onlyUnread, setOnlyUnread] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const query = useNotifications(false);
  const mark = useMarkNotificationRead();
  const archive = useArchiveNotifications();
  const items = (query.data?.items ?? []).filter((item) => !onlyUnread || !item.is_read).slice(0, SHOWN);

  const toggle = (item: NotificationItem) => {
    setExpanded((current) => (current === item.id ? null : item.id));
    if (!item.is_read) mark.mutate(item.id);
  };

  return (
    <div className="ui-notify-panel" role="dialog" aria-label={t("notifications.title")}>
      <div className="ui-notify-head">
        <strong>{t("notifications.title")}</strong>
        {unread > 0 && (
          <button type="button" className="ui-link-button" disabled={mark.isPending} onClick={() => mark.mutate("all")}>{t("notifications.readAll")}</button>
        )}
      </div>
      <div className="ui-chips ui-notify-filter" role="group" aria-label={t("notifications.title")}>
        <button type="button" className={`ui-chip${!onlyUnread ? " is-active" : ""}`} aria-pressed={!onlyUnread} onClick={() => setOnlyUnread(false)}>
          {t("notifications.show.all")}
        </button>
        <button type="button" className={`ui-chip${onlyUnread ? " is-active" : ""}`} aria-pressed={onlyUnread} onClick={() => setOnlyUnread(true)}>
          {t("notifications.show.unread")} <span className="ui-chip-count">{unread}</span>
        </button>
      </div>
      <div className="ui-notify-body">
        {query.error ? (
          <p className="ui-backup-error">{(query.error as Error).message}</p>
        ) : !query.data ? (
          <p className="ui-muted ui-notify-empty">{t("app.loading")}</p>
        ) : items.length === 0 ? (
          <p className="ui-muted ui-notify-empty">{t(onlyUnread ? "notifications.noUnread" : "notifications.empty")}</p>
        ) : (
          <ul className="ui-notify-list">
            {items.map((item) => (
              <Item key={item.id} item={item} open={expanded === item.id} onToggle={() => toggle(item)} busy={archive.isPending}
                onArchive={() => archive.mutate({ action: "archive", ids: [item.id] })} />
            ))}
          </ul>
        )}
      </div>
      <div className="ui-notify-foot">
        <Link to="/notifications" onClick={onClose}>{t("notifications.menu.seeAll")}</Link>
      </div>
    </div>
  );
}

function Item({ item, open, onToggle, onArchive, busy }: { item: NotificationItem; open: boolean; onToggle: () => void; onArchive: () => void; busy: boolean }) {
  const { t, i18n } = useTranslation();
  const newTab = { target: "_blank", rel: "noopener noreferrer" } as const;
  return (
    <li className={`ui-notify-item${item.is_read ? "" : " is-unread"} is-${item.severity}`}>
      <button type="button" className="ui-notify-row" aria-expanded={open} onClick={onToggle}>
        <span className="ui-notify-dot" aria-hidden="true" />
        <span className="ui-notify-text">
          <span className="ui-notify-title">{item.title}</span>
          {item.message && <span className={`ui-notify-message${open ? " is-open" : ""}`}>{item.message}</span>}
          <span className="ui-muted ui-notify-meta">
            {t(`notifications.categories.${item.category}`, { defaultValue: item.category })} · <span title={formatDateTime(item.created_at, i18n.language)}>{formatRelative(item.created_at, i18n.language)}</span>
            {item.device && <> · {item.device.name}</>}
          </span>
        </span>
      </button>
      {open && (
        <div className="ui-notify-actions">
          {item.device && (
            <a className="ui-button ui-button-ghost ui-button-small ui-with-brand" href={`/app/devices/${item.device.id}`} {...newTab}>
              <BrandIcon brand={item.device.brand} small /> {t("notifications.menu.openDevice")}
            </a>
          )}
          {item.advisory_id && (
            <a className="ui-button ui-button-ghost ui-button-small" href={`/app/vulnerabilities/${item.advisory_id}`} {...newTab}>{t("notifications.advisory")}</a>
          )}
          {item.source_url && (
            <a className="ui-button ui-button-ghost ui-button-small" href={item.source_url.startsWith("/") && !item.source_url.startsWith("/app/") ? `/app${item.source_url}` : item.source_url} {...newTab}>
              {t("notifications.source")}
            </a>
          )}
          <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={busy} onClick={onArchive}>{t("notifications.archive")}</button>
          <small className="ui-muted ui-block">{t("notifications.menu.newTab")}</small>
        </div>
      )}
    </li>
  );
}
