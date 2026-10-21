import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { useRouterosCatalog, useRouterosRefresh } from "../api/admin";
import { formatDateTime } from "../lib/format";
import { Loading } from "../app/States";

const CHANNEL_LABELS: Record<string, string> = {
  stable: "7 · stable", "long-term": "7 · long-term", testing: "7 · testing", development: "7 · development",
  "v6-stable": "6 · stable", "v6-long-term": "6 · long-term",
};

export function RouterosCatalog() {
  const { t, i18n } = useTranslation();
  const query = useRouterosCatalog();
  const refresh = useRouterosRefresh();
  const data = query.data;
  const label = (channel: string) => CHANNEL_LABELS[channel] ?? channel;
  return (
    <section className="ui-page">
      <nav className="ui-breadcrumbs" aria-label={t("device.breadcrumbs")}>
        <Link to="/integrations">{t("integrations.title")}</Link>
      </nav>
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("routeros.title")}</h1>
          <p>{t("routeros.subtitle")}{data?.last_refresh && ` ${t("routeros.lastRead", { when: formatDateTime(data.last_refresh, i18n.language) })}`}</p>
        </div>
        <div className="ui-row-actions">
          {data?.can_refresh && (
            <button type="button" className="ui-button" disabled={refresh.isPending} onClick={() => refresh.mutate()}>
              {refresh.isPending ? t("app.loading") : t("routeros.refresh")}
            </button>
          )}
        </div>
      </header>
      {refresh.data && (
        <div className={`ui-alert ${refresh.data.stats.errors.length ? "ui-alert-warning" : ""}`}>
          {t("routeros.refreshed", { channels: refresh.data.stats.channels, releases: refresh.data.stats.new_releases, updates: refresh.data.evaluated.updates, security: refresh.data.evaluated.security })}
          {refresh.data.stats.errors.length > 0 && ` ${t("routeros.errors", { errors: refresh.data.stats.errors.slice(0, 3).join("; ") })}`}
        </div>
      )}
      {refresh.error && <div className="ui-alert ui-alert-danger">{(refresh.error as Error).message}</div>}
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : (
        <>
          <section className="ui-panel ui-admin-section">
            {Object.keys(data.heads).length ? (
              <div className="ui-data-wrap">
                <table className="ui-data-table">
                  <thead><tr>{["channel", "version", "released", "security"].map((key) => <th key={key}><span className="ui-th">{t(`routeros.columns.${key}`)}</span></th>)}</tr></thead>
                  <tbody>
                    {data.channels.map((channel) => {
                      const head = data.heads[channel];
                      return (
                        <tr key={channel}>
                          <td>{label(channel)}</td>
                          <td><strong>{head?.version ?? "—"}</strong></td>
                          <td className="ui-nowrap">{head ? formatDateTime(head.released_at, i18n.language) : "—"}</td>
                          <td>
                            {!head ? <span className="ui-muted">{t("routeros.notRead")}</span> : head.security ? (
                              <>
                                <span className="ui-severity ui-severity-high">{t("routeros.securityBadge")}</span>
                                {head.security_lines.map((line) => <small key={line} className="ui-block">{line}</small>)}
                              </>
                            ) : "—"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="ui-empty">{data.can_refresh ? t("routeros.emptyAdmin") : t("routeros.empty")}</div>
            )}
          </section>
          {data.history.length > 0 && (
            <section className="ui-panel ui-admin-section">
              <h2>{t("routeros.history")}</h2>
              <div className="ui-data-wrap">
                <table className="ui-data-table">
                  <thead><tr>{["channel", "version", "released", "notes"].map((key) => <th key={key}><span className="ui-th">{t(`routeros.columns.${key}`)}</span></th>)}</tr></thead>
                  <tbody>
                    {data.history.map((release) => (
                      <tr key={`${release.channel}-${release.version}`}>
                        <td>{label(release.channel)}</td>
                        <td>{release.version} {release.security && <span className="ui-severity ui-severity-high">{t("routeros.securityBadge")}</span>}</td>
                        <td className="ui-nowrap">{formatDateTime(release.released_at, i18n.language)}</td>
                        <td>
                          {release.changelog ? (
                            <details><summary>{t("routeros.changelog")}</summary><pre className="ui-code-block">{release.changelog}</pre></details>
                          ) : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}
    </section>
  );
}
