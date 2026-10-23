import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { SearchSelect } from "../app/SearchSelect";
import type { TFunction } from "i18next";

import { useDeviceFacets } from "../api/devices";
import { BrandIcon } from "../app/BrandIcon";
import { FIRMWARE_STATES, type FirmwareFilters, type FirmwareRow, useFirmware, useFirmwareCheck } from "../api/operations";
import { useFirmwareAction } from "../api/deviceTabs";
import { formatRelative } from "../lib/format";
import { useDebounced } from "../lib/hooks";
import { Loading } from "../app/States";
import { Pager } from "../app/Pager";
import { pageParams, usePaging } from "../lib/paging";

const STATUS_TONE: Record<string, string> = {
  critical_security_update: "ui-severity ui-severity-critical",
  security_update: "ui-severity ui-severity-high",
  outdated: "ui-severity ui-severity-warning",
  update_available: "ui-severity ui-severity-warning",
};

export function Firmware() {
  const { t } = useTranslation();
  const search = useSearch({ from: "/firmware" });
  const navigate = useNavigate({ from: "/firmware" });
  const facets = useDeviceFacets();
  const [text, setText] = useState(search.q ?? "");
  const q = useDebounced(text.trim());
  const update = (patch: Partial<FirmwareFilters>) => void navigate({ search: (previous: FirmwareFilters) => ({ ...previous, ...patch }), replace: true });
  useEffect(() => {
    if ((search.q ?? "") !== q) update({ q: q || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);
  const paging = usePaging(JSON.stringify(search));
  const query = useFirmware(search, pageParams(paging));
  const rows = useMemo(() => query.data?.items ?? [], [query.data]);
  const first = query.data;
  const state = search.state ?? "attention";

  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("firmware.title")}</h1>
          <p>{t("firmware.subtitle")}</p>
        </div>
        <div className="ui-actions">
          <Link to="/firmware/suggestions" search={search.customer ? { customer: search.customer } : {}} className="ui-button ui-button-ghost">{t("firmware.suggestions")}</Link>
          <Link to="/firmware/catalogs" className="ui-button ui-button-ghost">{t("firmware.catalogs")}</Link>
        </div>
      </header>

      <div className="ui-chips" role="group" aria-label={t("firmware.states.label")}>
        {FIRMWARE_STATES.map((key) => (
          <button
            key={key}
            type="button"
            className={`ui-chip${["security", "critical", "unknown"].includes(key) ? " ui-chip-warning" : ""}${state === key ? " is-active" : ""}`}
            onClick={() => update({ state: key === "attention" ? undefined : key })}
          >
            {t(`firmware.states.${key}`)} <span className="ui-chip-count">{first ? first.counts[key] : "—"}</span>
          </button>
        ))}
      </div>
      <div className="ui-filter-bar">
        <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder={t("firmware.search")} aria-label={t("firmware.search")} autoComplete="off" />
        <SearchSelect value={search.customer ?? ""} options={(facets.data?.customers ?? []).map((customer) => ({ value: customer.id, label: customer.name }))} onChange={(value) => update({ customer: value || undefined })} emptyLabel={t("devices.allCustomers")} ariaLabel={t("devices.customer")} />
        <select value={search.vendor ?? ""} onChange={(event) => update({ vendor: event.target.value || undefined })} aria-label={t("devices.vendor")}>
          <option value="">{t("devices.allVendors")}</option>
          {facets.data?.vendors.map((vendor) => <option key={vendor} value={vendor}>{vendor}</option>)}
        </select>
      </div>

      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !first ? (
        <Loading />
      ) : rows.length ? (
        <>
          <div className="ui-data-wrap" aria-busy={query.isFetching}>
            <table className="ui-data-table">
              <thead>
                <tr>
                  {["device", "vendor", "installed", "recommended", "status", "verified", "actions"].map((column) => (
                    <th key={column}><span className="ui-th">{t(`firmware.columns.${column}`)}</span></th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => <Row key={row.id} row={row} />)}
              </tbody>
            </table>
          </div>
          <Pager total={first.total} paging={paging} busy={query.isFetching} />
        </>
      ) : (
        <div className="ui-empty">{state === "attention" ? t("firmware.allGood") : t("firmware.empty")}</div>
      )}
    </section>
  );
}

function Row({ row }: { row: FirmwareRow }) {
  const { t, i18n } = useTranslation();
  const check = useFirmwareCheck();
  const checked = check.isSuccess && check.variables === row.id;
  return (
    <tr>
      <td>
        <div className="ui-with-brand">
          <BrandIcon brand={row.brand} small />
          <span>
            <Link to="/devices/$deviceId" params={{ deviceId: row.id }} className="ui-cell-title">{row.name}</Link>
            <small className="ui-muted ui-block">{[row.customer?.name, row.site].filter(Boolean).join(" · ") || "—"}</small>
          </span>
        </div>
      </td>
      <td>
        {row.brand?.known ? row.brand.label : row.vendor ?? "—"}
        <small className="ui-muted ui-block">{[row.model, row.channel].filter(Boolean).join(" · ") || "—"}</small>
      </td>
      <td className="ui-mono">{row.firmware ?? "—"}</td>
      <td>
        {row.newer ? <strong className="ui-text-warning ui-mono">{row.recommended}</strong> : <span className="ui-mono">{row.recommended ?? "—"}</span>}
        {row.readiness && <small className="ui-muted ui-block">{routerosStatus(t, row.readiness)}</small>}
      </td>
      <td>
        <span className={STATUS_TONE[row.firmware_status] ?? "ui-severity"}>{t(`firmware.status.${row.firmware_status}`, { defaultValue: row.firmware_status })}</span>
      </td>
      <td className="ui-nowrap">{row.verified_at ? formatRelative(row.verified_at, i18n.language) : <span className="ui-muted">{t("firmware.notVerified")}</span>}</td>
      <td>
        <div className="ui-row-actions">
          {row.can_check && (
            <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={check.isPending} onClick={() => check.mutate(row.id)}>
              {checked ? t(`firmware.check.${check.data?.state}`, { defaultValue: t("firmware.check.queued") }) : t("firmware.check.now")}
            </button>
          )}
          {row.can_plan && <PreparePlan deviceId={row.id} />}
          <Link className="ui-button ui-button-ghost ui-button-small" to="/devices/$deviceId/firmware-upgrade" params={{ deviceId: row.id }}>{t("firmware.plan")}</Link>
        </div>
        {check.error && check.variables === row.id && <small className="ui-backup-error">{(check.error as Error).message}</small>}
      </td>
    </tr>
  );
}

/** Opens (or reuses) the upgrade plan of a device, then shows it. */
function PreparePlan({ deviceId }: { deviceId: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const create = useFirmwareAction(deviceId, "firmware-upgrade/plan");
  return (
    <>
      <button type="button" className="ui-button ui-button-primary ui-button-small" disabled={create.isPending}
        onClick={() => create.mutate(undefined, { onSuccess: () => void navigate({ to: "/devices/$deviceId/firmware-upgrade", params: { deviceId } }) })}>
        {t("firmware.prepare")}
      </button>
      {create.error && <small className="ui-backup-error ui-block">{(create.error as Error).message}</small>}
    </>
  );
}

const ROUTEROS_STATUS: Record<string, string> = {
  "New version is available": "newVersion",
  "System is already up to date": "upToDate",
  "finding out latest version...": "checking",
  "Downloaded, please reboot router to upgrade it": "downloaded",
  "ERROR: could not resolve dns name": "dnsError",
};

/** The update status RouterOS reports, translated when it is one of the known messages. */
function routerosStatus(t: TFunction, status: string) {
  const key = ROUTEROS_STATUS[status];
  return key ? t(`firmware.routeros.${key}`) : status;
}
