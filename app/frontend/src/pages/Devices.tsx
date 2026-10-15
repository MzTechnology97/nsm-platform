import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { type DeviceFilters, type DeviceRow, useDeviceFacets, useDevices } from "../api/devices";
import { can, useSession } from "../api/session";
import { BrandIcon } from "../app/BrandIcon";
import { formatRelative } from "../lib/format";
import { useDebounced } from "../lib/hooks";

const STATUS_CHIPS = [
  { key: "", count: "all" },
  { key: "online", count: "online" },
  { key: "offline", count: "offline" },
  { key: "pending", count: "pending" },
] as const;
const ATTENTION_CHIPS = [
  { key: "firmware", value: "attention", count: "firmware_attention" },
  { key: "security", value: "severe", count: "severe" },
  { key: "lifecycle", value: "eol", count: "eol" },
] as const;
const SORTABLE = ["name", "status", "vendor", "customer", "ip", "firmware", "last_seen"] as const;
const ROW_HEIGHT = 56;

export function Devices() {
  const { t, i18n } = useTranslation();
  const search = useSearch({ from: "/devices" }) as DeviceFilters;
  const navigate = useNavigate({ from: "/devices" });
  const session = useSession();
  const [text, setText] = useState(search.q ?? "");
  const q = useDebounced(text);
  const facets = useDeviceFacets();

  const update = (patch: Partial<DeviceFilters>) =>
    void navigate({ search: (previous: DeviceFilters) => ({ ...previous, ...patch }), replace: true });

  useEffect(() => {
    if ((search.q ?? "") !== q) update({ q: q || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const filters = useMemo<DeviceFilters>(() => ({ ...search, sort: search.sort ?? "name", dir: search.dir ?? "asc" }), [search]);
  const query = useDevices(filters);
  const rows = useMemo(() => query.data?.pages.flatMap((page) => page.items) ?? [], [query.data]);
  const first = query.data?.pages[0];
  const total = first?.total ?? 0;
  const counts = first?.counts;

  const parent = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({ count: rows.length, getScrollElement: () => parent.current, estimateSize: () => ROW_HEIGHT, overscan: 12 });
  const items = virtualizer.getVirtualItems();
  const last = items[items.length - 1];
  useEffect(() => {
    if (last && last.index >= rows.length - 20 && query.hasNextPage && !query.isFetchingNextPage) void query.fetchNextPage();
  }, [last, rows.length, query]);

  const sites = (facets.data?.sites ?? []).filter((site) => !filters.customer || site.customer_id === filters.customer);

  const sortBy = (column: (typeof SORTABLE)[number]) =>
    update({ sort: column, dir: filters.sort === column && filters.dir === "asc" ? "desc" : "asc" });

  const canImport = can(session.data, "users.manage");

  return (
    <section className="ui-page ui-devices">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("devices.title")}</h1>
          <p>{t("devices.subtitle", { count: total })}</p>
        </div>
        <div className="ui-actions">
          {canImport && <Link className="ui-button ui-button-ghost" to="/devices/import">{t("deviceImport.open")}</Link>}
          <a className="ui-button ui-button-ghost" href="/devices">
            {t("app.classic")}
          </a>
        </div>
      </header>

      <div className="ui-chips" role="group" aria-label={t("devices.status")}>
        {STATUS_CHIPS.map((chip) => (
          <button
            key={chip.count}
            type="button"
            className={`ui-chip${(filters.status ?? "") === chip.key ? " is-active" : ""}`}
            onClick={() => update({ status: chip.key || undefined })}
          >
            {t(`devices.chips.${chip.count}`)} <span className="ui-chip-count">{counts?.[chip.count] ?? "—"}</span>
          </button>
        ))}
        <span className="ui-chip-separator" />
        {ATTENTION_CHIPS.map((chip) => {
          const active = filters[chip.key] === chip.value;
          return (
            <button
              key={chip.key}
              type="button"
              className={`ui-chip ui-chip-warning${active ? " is-active" : ""}`}
              onClick={() => update({ [chip.key]: active ? undefined : chip.value })}
            >
              {t(`devices.chips.${chip.count}`)} <span className="ui-chip-count">{counts?.[chip.count] ?? "—"}</span>
            </button>
          );
        })}
      </div>

      <div className="ui-filter-bar">
        <input
          type="search"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={t("devices.searchPlaceholder")}
          aria-label={t("devices.search")}
        />
        <select value={filters.customer ?? ""} onChange={(event) => update({ customer: event.target.value || undefined, site: undefined })} aria-label={t("devices.customer")}>
          <option value="">{t("devices.allCustomers")}</option>
          {facets.data?.customers.map((customer) => (
            <option key={customer.id} value={customer.id}>
              {customer.name}
            </option>
          ))}
        </select>
        <select value={filters.site ?? ""} onChange={(event) => update({ site: event.target.value || undefined })} aria-label={t("devices.site")} disabled={!sites.length}>
          <option value="">{t("devices.allSites")}</option>
          {sites.map((site) => (
            <option key={site.id} value={site.id}>
              {site.name}
            </option>
          ))}
        </select>
        <select value={filters.vendor ?? ""} onChange={(event) => update({ vendor: event.target.value || undefined })} aria-label={t("devices.vendor")}>
          <option value="">{t("devices.allVendors")}</option>
          {facets.data?.vendors.map((vendor) => (
            <option key={vendor} value={vendor}>
              {vendor}
            </option>
          ))}
        </select>
        {(Object.keys(search).length > 0 || text) && (
          <button
            type="button"
            className="ui-button ui-button-ghost"
            onClick={() => {
              setText("");
              void navigate({ search: {}, replace: true });
            }}
          >
            {t("devices.reset")}
          </button>
        )}
      </div>

      <div className="ui-table" role="table" aria-rowcount={total} aria-busy={query.isFetching}>
        <div className="ui-table-head ui-device-grid" role="row">
          {SORTABLE.map((column) => (
            <button key={column} type="button" role="columnheader" className="ui-th" onClick={() => sortBy(column)} aria-sort={filters.sort === column ? (filters.dir === "asc" ? "ascending" : "descending") : "none"}>
              {t(`devices.columns.${column}`)}
              {filters.sort === column ? (filters.dir === "asc" ? " ▲" : " ▼") : ""}
            </button>
          ))}
        </div>
        {query.isError ? (
          <div className="ui-empty">
            {t("app.error")}{" "}
            <button type="button" className="ui-button" onClick={() => void query.refetch()}>
              {t("app.retry")}
            </button>
          </div>
        ) : !query.isPending && rows.length === 0 ? (
          <div className="ui-empty">{t("devices.empty")}</div>
        ) : (
          <div ref={parent} className="ui-table-body">
            <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
              {items.map((item) => {
                const device = rows[item.index];
                return (
                  <DeviceLine key={device.id} device={device} top={item.start} height={item.size} locale={i18n.language} />
                );
              })}
            </div>
          </div>
        )}
        <div className="ui-table-foot">
          {query.isFetching ? t("app.loading") : t("devices.shown", { shown: rows.length, total })}
        </div>
      </div>
    </section>
  );
}

function DeviceLine({ device, top, height, locale }: { device: DeviceRow; top: number; height: number; locale: string }) {
  const { t } = useTranslation();
  return (
    <Link className="ui-tr ui-device-grid" role="row" to="/devices/$deviceId" params={{ deviceId: device.id }} style={{ transform: `translateY(${top}px)`, height }}>
      <span role="cell" className="ui-with-brand">
        <BrandIcon brand={device.brand} small />
        <span className="ui-td-main">
          <strong>{device.name}</strong>
          <small>{device.model ?? device.device_type ?? ""}</small>
        </span>
      </span>
      <span role="cell">
        <span className={`ui-status ui-status-${device.status}`}>{t(`devices.statusLabel.${device.status}`, { defaultValue: device.status })}</span>
      </span>
      <span role="cell">{device.vendor}</span>
      <span role="cell" className="ui-td-main">
        <span>{device.customer?.name ?? "—"}</span>
        <small>{device.site?.name ?? ""}</small>
      </span>
      <span role="cell" className="ui-mono">{device.management_ip ?? "—"}</span>
      <span role="cell">
        {device.firmware ?? "—"}
        {device.severe_cves > 0 && <span className="ui-badge ui-badge-danger" title={t("devices.severeCves")}>{device.severe_cves} CVE</span>}
      </span>
      <span role="cell" title={device.last_seen ?? ""}>{formatRelative(device.last_seen, locale)}</span>
    </Link>
  );
}
