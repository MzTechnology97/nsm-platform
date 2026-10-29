import { useTranslation } from "react-i18next";

import { PAGE_SIZES, type Paging } from "../lib/paging";

/** Pages of a list: "1–50 of 838", first/previous/numbers/next/last and rows per page (50, 100, 200). */
export function Pager({ total, paging, busy = false }: { total: number; paging: Paging; busy?: boolean }) {
  const { t, i18n } = useTranslation();
  const pages = Math.max(1, Math.ceil(total / paging.size));
  if (total <= PAGE_SIZES[0] && paging.page === 1) return null;
  const page = Math.min(paging.page, pages);
  const from = total ? (page - 1) * paging.size + 1 : 0;
  const to = Math.min(total, page * paging.size);
  const number = new Intl.NumberFormat(i18n.language);
  // Window of page numbers around the current one, with the first and the last always reachable.
  const around = [...new Set([1, page - 2, page - 1, page, page + 1, page + 2, pages])].filter((n) => n >= 1 && n <= pages).sort((a, b) => a - b);
  return (
    <nav className="ui-pager" aria-label={t("pager.label")} aria-busy={busy}>
      <span className="ui-pager-range">{t("pager.range", { from: number.format(from), to: number.format(to), total: number.format(total) })}</span>
      <div className="ui-pager-pages">
        <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={page <= 1} onClick={() => paging.setPage(page - 1)}
          aria-label={t("pager.previous")}>‹</button>
        {around.map((n, index) => (
          <span key={n} className="ui-pager-slot">
            {index > 0 && n - around[index - 1] > 1 && <span className="ui-pager-gap" aria-hidden="true">…</span>}
            <button type="button" className={`ui-button ui-button-small${n === page ? "" : " ui-button-ghost"}`} aria-current={n === page ? "page" : undefined}
              onClick={() => paging.setPage(n)}>{number.format(n)}</button>
          </span>
        ))}
        <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={page >= pages} onClick={() => paging.setPage(page + 1)}
          aria-label={t("pager.next")}>›</button>
      </div>
      <label className="ui-pager-size">
        <span>{t("pager.size")}</span>
        <select value={paging.size} onChange={(event) => paging.setSize(Number(event.target.value))}>
          {PAGE_SIZES.map((size) => <option key={size} value={size}>{size}</option>)}
        </select>
      </label>
    </nav>
  );
}
