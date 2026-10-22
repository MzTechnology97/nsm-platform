import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { type NotificationItem, useArchiveNotifications, useMarkNotificationRead, useNotifications } from "../api/personal";
import { BrandIcon } from "../app/BrandIcon";
import { formatDateTime } from "../lib/format";
import { Loading } from "../app/States";

type Show = "all" | "unread" | "archived";

/** Personal notification centre: active notifications newest first, with the read state of the signed-in user; each user can archive theirs. */
export function Notifications() {
  const { t } = useTranslation();
  const [show, setShow] = useState<Show>("all");
  const query = useNotifications(show === "archived");
  const mark = useMarkNotificationRead();
  const archive = useArchiveNotifications();
  const data = query.data;
  const items = data ? data.items.filter((item) => show !== "unread" || !item.is_read) : [];
  const readCount = data && show !== "archived" ? data.items.filter((item) => item.is_read).length : 0;
  const counts: Record<Show, number> = { all: show === "archived" ? 0 : data?.items.length ?? 0, unread: data?.unread ?? 0, archived: data?.archived ?? 0 };

  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("notifications.title")}</h1>
          <p>{t("notifications.subtitle")}</p>
        </div>
        {data && show !== "archived" && (
          <div className="ui-row-actions">
            {data.unread > 0 && (
              <button type="button" className="ui-button ui-button-ghost" disabled={mark.isPending} onClick={() => mark.mutate("all")}>
                {t("notifications.readAll")}
              </button>
            )}
            {readCount > 0 && (
              <button type="button" className="ui-button ui-button-ghost" disabled={archive.isPending} onClick={() => archive.mutate({ action: "archive-read" })}>
                {t("notifications.archiveRead", { count: readCount })}
              </button>
            )}
          </div>
        )}
      </header>
      {data && (
        <div className="ui-chips" role="group" aria-label={t("notifications.title")}>
          {(["all", "unread", "archived"] as const).map((key) => (
            <button key={key} type="button" className={`ui-chip${show === key ? " is-active" : ""}`} aria-pressed={show === key} onClick={() => setShow(key)}>
              {t(`notifications.show.${key}`)}{key !== "all" || show !== "archived" ? <> <span className="ui-chip-count">{counts[key]}</span></> : null}
            </button>
          ))}
        </div>
      )}
      {(mark.error || archive.error) && <div className="ui-alert ui-alert-danger">{((mark.error ?? archive.error) as Error).message}</div>}
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : items.length ? (
        <ul className="ui-notification-list">
          {items.map((item) => (
            <Row key={item.id} item={item} archived={show === "archived"} busy={mark.isPending || archive.isPending} onRead={() => mark.mutate(item.id)}
              onArchive={() => archive.mutate({ action: show === "archived" ? "unarchive" : "archive", ids: [item.id] })} />
          ))}
        </ul>
      ) : (
        <div className="ui-empty">{t(show === "unread" ? "notifications.noUnread" : show === "archived" ? "notifications.noArchived" : "notifications.empty")}</div>
      )}
    </section>
  );
}

function Row({ item, archived, busy, onRead, onArchive }: { item: NotificationItem; archived: boolean; busy: boolean; onRead: () => void; onArchive: () => void }) {
  const { t, i18n } = useTranslation();
  const external = item.source_url?.startsWith("http");
  return (
    <li className={`ui-notification${item.is_read ? "" : " is-unread"} is-${item.severity}`}>
      <div className="ui-notification-body">
        <div className="ui-notification-title">
          <strong>{item.title}</strong>
          {item.maintenance && (
            <span className="ui-chip" title={t("notifications.maintenanceHint", { reason: item.maintenance.reason, until: formatDateTime(item.maintenance.ends_at, i18n.language) })}>
              {t("notifications.maintenance")}
            </span>
          )}
          <span className={`ui-severity ui-severity-${item.severity}`}>{t(`actionCenter.severities.${item.severity}`, { defaultValue: item.severity })}</span>
          {!item.is_read && <span className="ui-chip ui-chip-warning">{t("notifications.new")}</span>}
        </div>
        {item.message && <p>{item.message}</p>}
        <small className="ui-muted">
          {t(`notifications.categories.${item.category}`, { defaultValue: item.category })} · {formatDateTime(item.created_at, i18n.language)}
        </small>
        {item.device && (
          <span className="ui-with-brand">
            <BrandIcon brand={item.device.brand} small />
            <Link to="/devices/$deviceId" params={{ deviceId: item.device.id }}>{item.device.name}</Link>
          </span>
        )}
      </div>
      <div className="ui-row-actions">
        {item.advisory_id && (
          <Link to="/vulnerabilities/$advisoryId" params={{ advisoryId: item.advisory_id }} className="ui-button ui-button-ghost ui-button-small">
            {t("notifications.advisory")}
          </Link>
        )}
        {item.source_url && (
          <a className="ui-button ui-button-ghost ui-button-small" href={item.source_url} {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}>
            {t("notifications.source")}
          </a>
        )}
        {!item.is_read && !archived && (
          <button type="button" className="ui-button ui-button-small" disabled={busy} onClick={onRead}>{t("notifications.markRead")}</button>
        )}
        <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={busy} onClick={onArchive}>
          {t(archived ? "notifications.restore" : "notifications.archive")}
        </button>
      </div>
    </li>
  );
}
