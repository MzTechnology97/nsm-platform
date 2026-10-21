import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Icon } from "./Icon";

/**
 * Placeholder shown while a page or a table loads: grey bars where the content will be, so the layout does not jump
 * and an empty list is never mistaken for "no results". Screen readers hear "Loading…".
 */
export function Loading({ variant = "page", rows = 6 }: { variant?: "page" | "table" | "inline"; rows?: number }) {
  const { t } = useTranslation();
  if (variant === "inline") {
    return (
      <span className="ui-skeleton-inline" role="status">
        <span className="ui-skeleton ui-skeleton-text" />
        <span className="ui-visually-hidden">{t("app.loading")}</span>
      </span>
    );
  }
  return (
    <div className={`ui-skeleton-block is-${variant}`} role="status" aria-live="polite">
      <span className="ui-visually-hidden">{t("app.loading")}</span>
      {variant === "page" && (
        <div className="ui-skeleton-cards" aria-hidden="true">
          {[0, 1, 2, 3].map((key) => <span key={key} className="ui-skeleton ui-skeleton-card" />)}
        </div>
      )}
      <div className="ui-skeleton-rows" aria-hidden="true">
        {Array.from({ length: rows }, (_, key) => (
          <span key={key} className="ui-skeleton-row">
            <span className="ui-skeleton ui-skeleton-dot" />
            <span className="ui-skeleton ui-skeleton-text is-wide" />
            <span className="ui-skeleton ui-skeleton-text" />
            <span className="ui-skeleton ui-skeleton-text is-short" />
          </span>
        ))}
      </div>
    </div>
  );
}

/** Empty list with a reason and, when useful, what to do next (clear filters, add the first item…). */
export function EmptyState({ icon = "search", title, hint, action }: { icon?: string; title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="ui-empty-state">
      <span className="ui-empty-icon" aria-hidden="true"><Icon name={icon} size={22} /></span>
      <strong>{title}</strong>
      {hint && <p>{hint}</p>}
      {action && <div className="ui-empty-action">{action}</div>}
    </div>
  );
}
