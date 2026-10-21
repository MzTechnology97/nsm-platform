import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { type NotificationItem, useMarkNotificationRead, useNotifications } from "../api/personal";
import { BrandIcon } from "../app/BrandIcon";
import { formatDateTime } from "../lib/format";
import { Loading } from "../app/States";

type Show = "all" | "unread";

/** Personal notification centre: active notifications newest first, with the read state of the signed-in user. */
export function Notifications() {
  const { t } = useTranslation();
  const query = useNotifications();
  const mark = useMarkNotificationRead();
  const [show, setShow] = useState<Show>("all");
  const data = query.data;
  const items = data ? data.items.filter((item) => show === "all" || !item.is_read) : [];

  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("notifications.title")}</h1>
          <p>{t("notifications.subtitle")}</p>
        </div>
        {data && data.unread > 0 && (
          <button type="button" className="ui-button ui-button-ghost" disabled={mark.isPending} onClick={() => mark.mutate("all")}>
            {t("notifications.readAll")}
          </button>
        )}
      </header>
      {data && (
        <div className="ui-chips" role="group" aria-label={t("notifications.title")}>
          {(["all", "unread"] as const).map((key) => (
            <button key={key} type="button" className={`ui-chip${show === key ? " is-active" : ""}`} aria-pressed={show === key} onClick={() => setShow(key)}>
              {t(`notifications.show.${key}`)} <span className="ui-chip-count">{key === "all" ? data.items.length : data.unread}</span>
            </button>
          ))}
        </div>
      )}
      {mark.error && <div className="ui-alert ui-alert-danger">{(mark.error as Error).message}</div>}
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : items.length ? (
        <ul className="ui-notification-list">
          {items.map((item) => (
            <Row key={item.id} item={item} busy={mark.isPending} onRead={() => mark.mutate(item.id)} />
          ))}
        </ul>
      ) : (
        <div className="ui-empty">{t(show === "unread" ? "notifications.noUnread" : "notifications.empty")}</div>
      )}
    </section>
  );
}

function Row({ item, busy, onRead }: { item: NotificationItem; busy: boolean; onRead: () => void }) {
  const { t, i18n } = useTranslation();
  const external = item.source_url?.startsWith("http");
  return (
    <li className={`ui-notification${item.is_read ? "" : " is-unread"} is-${item.severity}`}>
      <div className="ui-notification-body">
        <div className="ui-notification-title">
          <strong>{item.title}</strong>
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
        {!item.is_read && (
          <button type="button" className="ui-button ui-button-small" disabled={busy} onClick={onRead}>{t("notifications.markRead")}</button>
        )}
      </div>
    </li>
  );
}
