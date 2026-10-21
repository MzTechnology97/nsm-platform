import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useVirtualizer } from "@tanstack/react-virtual";
import { type MouseEvent, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { type DeviceCounts, type DeviceFilters, type DeviceRow, devicesExportUrl, useDeviceFacets, useDevices } from "../api/devices";
import { can, useSession } from "../api/session";
import { BrandIcon } from "../app/BrandIcon";
import { Resources } from "../app/Resources";
import { RingTile, type TileTone } from "../app/RingTile";
import { EmptyState, Loading } from "../app/States";
import { formatDuration, formatRelative } from "../lib/format";
import { useDebounced } from "../lib/hooks";

const STATUS_CHIPS = [
  { key: "", count: "all" },
  { key: "online", count: "online" },
  { key: "offline", count: "offline" },
  { key: "pending", count: "pending" },
  { key: "decommissioned", count: "decommissioned" },
] as const;
const ATTENTION_CHIPS = [
  { key: "firmware", value: "attention", count: "firmware_attention" },
  { key: "security", value: "severe", count: "severe" },
  { key: "lifecycle", value: "eol", count: "eol" },
] as const;
/** Summary tiles over the visible inventory (the same figures as the chips). */
const KPI_TILES = [
  { count: "online", tone: "success" },
  { count: "offline", tone: "danger" },
  { count: "pending", tone: "warning" },
  { count: "severe", tone: "danger" },
  { count: "firmware_attention", tone: "warning" },
  { count: "eol", tone: "warning" },
] as const satisfies readonly { count: keyof DeviceCounts; tone: TileTone }[];
const SORTABLE = ["name", "status", "customer", "ip", "firmware", "last_seen"] as const;
type Sortable = (typeof SORTABLE)[number];
/** Requests run in parallel by a bulk action. */
const BULK_PARALLEL = 3;
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
  const activeFilters = Object.entries(search).filter(([key, value]) => value && key !== "sort" && key !== "dir").length;
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

  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  useEffect(() => setSelected(new Set()), [filters]);
  const toggle = (id: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const allSelected = rows.length > 0 && rows.every((row) => selected.has(row.id));
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(rows.map((row) => row.id)));
  const vendorName = (vendor: string) => (vendor === "generic" ? t("devices.genericVendor") : facets.data?.vendor_labels?.[vendor] ?? vendor);

  const sortBy = (column: Sortable) =>
    update({ sort: column, dir: filters.sort === column && filters.dir === "asc" ? "desc" : "asc" });

  const canImport = can(session.data, "users.manage");
  const filtered = Object.keys(search).length > 0 || Boolean(text);
  const resetFilters = () => {
    setText("");
    void navigate({ search: {}, replace: true });
  };

  return (
    <section className="ui-page ui-devices">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("devices.title")}</h1>
          <p>{query.isPending ? t("app.loading") : t("devices.subtitle", { count: total })}</p>
        </div>
        <div className="ui-actions">
          {total > 0 && <a className="ui-button ui-button-ghost" href={devicesExportUrl(filters)} download>{t("devices.export")}</a>}
          {canImport && <Link className="ui-button ui-button-ghost" to="/devices/import">{t("deviceImport.open")}</Link>}
        </div>
      </header>

      {counts && counts.all > 0 && (
        <div className="ui-metrics ui-ring-tiles ui-device-kpis">
          {KPI_TILES.map((tile) => {
            const value = counts[tile.count];
            const percent = (100 * value) / counts.all;
            return (
              <RingTile key={tile.count} label={t(`devices.chips.${tile.count}`)} value={String(value)} percent={percent}
                note={t("devices.share", { percent: new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 0 }).format(percent) })}
                tone={tile.tone === "success" ? "success" : value ? tile.tone : "success"} />
            );
          })}
        </div>
      )}

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
              {vendorName(vendor)}
            </option>
          ))}
        </select>
        {filtered && (
          <button type="button" className="ui-button ui-button-ghost" onClick={resetFilters}>
            {t("devices.reset")}{activeFilters > 0 && <span className="ui-chip-count">{activeFilters}</span>}
          </button>
        )}
      </div>

      {selected.size > 0 && (
        <SelectionBar ids={[...selected]} rows={rows} filters={filters} retired={filters.status === "decommissioned"} onClear={() => setSelected(new Set())} />
      )}

      <div className="ui-table" role="table" aria-rowcount={total} aria-busy={query.isFetching}>
        <div className="ui-table-head ui-device-grid" role="row">
          <span role="columnheader" className="ui-th ui-select-cell">
            <Checkbox checked={allSelected} label={t("devices.selectAll")} onToggle={toggleAll} />
          </span>
          {(["name", "status", "customer", "ip", "firmware"] as const).map((column) => (
            <SortHeader key={column} column={column} filters={filters} onSort={sortBy} />
          ))}
          <span role="columnheader" className="ui-th">{t("devices.columns.resources")}</span>
          <SortHeader column="last_seen" filters={filters} onSort={sortBy} />
        </div>
        {query.isError ? (
          <div className="ui-empty">
            {t("app.error")}{" "}
            <button type="button" className="ui-button" onClick={() => void query.refetch()}>
              {t("app.retry")}
            </button>
          </div>
        ) : query.isPending ? (
          <Loading variant="table" rows={8} />
        ) : rows.length === 0 ? (
          filtered ? (
            <EmptyState title={t("devices.empty")} hint={t("devices.emptyHint")}
              action={<button type="button" className="ui-button" onClick={resetFilters}>{t("devices.reset")}</button>} />
          ) : (
            <EmptyState icon="devices" title={t("devices.none")} hint={t("devices.noneHint")} />
          )
        ) : (
          <div ref={parent} className="ui-table-body">
            <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
              {items.map((item) => {
                const device = rows[item.index];
                return (
                  <DeviceLine key={device.id} device={device} top={item.start} height={item.size} locale={i18n.language}
                    selected={selected.has(device.id)} onToggle={toggle} vendorName={vendorName} />
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

function SortHeader({ column, filters, onSort }: { column: Sortable; filters: DeviceFilters; onSort: (column: Sortable) => void }) {
  const { t } = useTranslation();
  return (
    <button type="button" role="columnheader" className="ui-th" onClick={() => onSort(column)}
      aria-sort={filters.sort === column ? (filters.dir === "asc" ? "ascending" : "descending") : "none"}>
      {t(`devices.columns.${column}`)}
      {filters.sort === column ? (filters.dir === "asc" ? " ▲" : " ▼") : ""}
    </button>
  );
}

/** Checkbox drawn as a button: the row is a link, so a native input inside it would also navigate. */
function Checkbox({ checked, label, onToggle }: { checked: boolean; label: string; onToggle: () => void }) {
  const click = (event: MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    onToggle();
  };
  return (
    <button type="button" role="checkbox" aria-checked={checked} aria-label={label} title={label} className={`ui-checkbox${checked ? " is-checked" : ""}`}
      onClick={click} />
  );
}

/** CVE state of a device: red with the open count, green when evaluated and clean, grey when NSM cannot tell. */
function CveBadge({ cves }: { cves: DeviceRow["cves"] }) {
  const { t } = useTranslation();
  if (!cves) return null;
  if (cves.open > 0) {
    return (
      <span className={`ui-cve-badge ${cves.severe > 0 ? "is-severe" : "is-open"}`} title={t("devices.cve.openTitle", { count: cves.open, severe: cves.severe })}>
        CVE {cves.open}
      </span>
    );
  }
  return cves.evaluated
    ? <span className="ui-cve-badge is-clean" title={t("devices.cve.cleanTitle")}>CVE OK</span>
    : <span className="ui-cve-badge is-unknown" title={t("devices.cve.unknownTitle")}>CVE ?</span>;
}

/** CPU and memory use as two thin bars with their value. */
function DeviceLine({ device, top, height, locale, selected, onToggle, vendorName }: {
  device: DeviceRow; top: number; height: number; locale: string; selected: boolean; onToggle: (id: string) => void; vendorName: (vendor: string) => string;
}) {
  const { t } = useTranslation();
  const uptime = device.telemetry?.uptime_seconds;
  return (
    <Link className={`ui-tr ui-device-grid${selected ? " is-selected" : ""}`} role="row" to="/devices/$deviceId" params={{ deviceId: device.id }}
      style={{ transform: `translateY(${top}px)`, height }}>
      <span role="cell" className="ui-select-cell">
        <Checkbox checked={selected} label={t("devices.select", { name: device.name })} onToggle={() => onToggle(device.id)} />
      </span>
      <span role="cell" className="ui-with-brand">
        <BrandIcon brand={device.brand} small />
        <span className="ui-td-main">
          <strong>{device.name}</strong>
          <small>{[vendorName(device.vendor), device.model ?? device.device_type].filter(Boolean).join(" · ")}</small>
        </span>
      </span>
      <span role="cell">
        {device.decommissioned_at ? (
          <span className="ui-status ui-status-decommissioned">{t("decommission.badge")}</span>
        ) : (
          <span className={`ui-status ui-status-${device.status}`}>{t(`devices.statusLabel.${device.status}`, { defaultValue: device.status })}</span>
        )}
        {device.signal_dbm != null ? (
          <small className="ui-muted ui-block">{t("devices.signal", { value: device.signal_dbm })}</small>
        ) : uptime != null && device.status === "online" ? (
          <small className="ui-muted ui-block" title={t("devices.uptime")}>{t("devices.uptimeShort", { value: formatDuration(uptime, locale) })}</small>
        ) : null}
      </span>
      <span role="cell" className="ui-td-main">
        <span>{device.customer?.name ?? "—"}</span>
        <small>{device.site?.name ?? ""}</small>
      </span>
      <span role="cell" className="ui-mono">
        {device.management_ip ?? "—"}
        {device.lan_ip && device.lan_ip !== device.management_ip && <small className="ui-muted ui-block">{t("devices.lan", { ip: device.lan_ip })}</small>}
      </span>
      <span role="cell" className="ui-firmware-cell">
        <span>{device.firmware ?? "—"}</span>
        <CveBadge cves={device.cves} />
      </span>
      <span role="cell">
        <Resources telemetry={device.telemetry} online={device.status === "online"} />
      </span>
      <span role="cell" title={device.last_seen ?? ""}>{formatRelative(device.last_seen, locale)}</span>
    </Link>
  );
}

type BulkAction = "backup" | "firmware" | "decommission" | "reactivate";
interface BulkResult { done: number; total: number; failed: { name: string; message: string }[]; running: boolean }

/** Actions on the selected devices; each runs the same request the device page sends, a few at a time. */
function SelectionBar({ ids, rows, filters, retired, onClear }: { ids: string[]; rows: DeviceRow[]; filters: DeviceFilters; retired: boolean; onClear: () => void }) {
  const { t } = useTranslation();
  const session = useSession();
  const client = useQueryClient();
  const [result, setResult] = useState<BulkResult | null>(null);
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  const byId = useMemo(() => new Map(rows.map((row) => [row.id, row])), [rows]);
  const chosen = ids.map((id) => byId.get(id)).filter((row): row is DeviceRow => Boolean(row));
  const mikrotik = chosen.filter((row) => row.vendor === "mikrotik");

  const run = async (action: BulkAction, targets: DeviceRow[], body?: object) => {
    const state: BulkResult = { done: 0, total: targets.length, failed: [], running: true };
    setResult({ ...state });
    const path = (row: DeviceRow) => ({
      backup: `/api/ui/v1/devices/${row.id}/backups/run`,
      firmware: `/api/ui/v1/firmware/${row.id}/check`,
      decommission: `/api/ui/v1/devices/${row.id}/decommission`,
      reactivate: `/api/ui/v1/devices/${row.id}/reactivate`,
    })[action];
    const queue = [...targets];
    const worker = async () => {
      for (let row = queue.shift(); row; row = queue.shift()) {
        try {
          await api(path(row), { method: "POST", body: JSON.stringify(body ?? {}) });
        } catch (error) {
          state.failed.push({ name: row.name, message: (error as Error).message });
        }
        state.done += 1;
        setResult({ ...state, failed: [...state.failed] });
      }
    };
    await Promise.all(Array.from({ length: Math.min(BULK_PARALLEL, targets.length) }, worker));
    setResult({ ...state, running: false, failed: [...state.failed] });
    void client.invalidateQueries({ queryKey: ["devices"] });
  };

  const busy = result?.running ?? false;
  return (
    <div className="ui-selection-bar" role="region" aria-label={t("devices.bulk.title")}>
      <strong>{t("devices.bulk.selected", { count: ids.length })}</strong>
      <div className="ui-selection-actions">
        <a className="ui-button ui-button-small" href={devicesExportUrl(filters, ids)} download>{t("devices.bulk.export")}</a>
        {!retired && can(session.data, "backup.execute") && (
          <button type="button" className="ui-button ui-button-small" disabled={busy} onClick={() => void run("backup", chosen)}>{t("devices.bulk.backup")}</button>
        )}
        {!retired && mikrotik.length > 0 && can(session.data, "firmware.read") && (
          <button type="button" className="ui-button ui-button-small" disabled={busy} onClick={() => void run("firmware", mikrotik)}
            title={mikrotik.length < chosen.length ? t("devices.bulk.mikrotikOnly", { count: mikrotik.length }) : undefined}>
            {t("devices.bulk.firmware")}
          </button>
        )}
        {can(session.data, "devices.write") && (retired ? (
          <button type="button" className="ui-button ui-button-small" disabled={busy} onClick={() => void run("reactivate", chosen)}>{t("decommission.reactivate")}</button>
        ) : (
          <button type="button" className="ui-button ui-button-small ui-button-danger" disabled={busy} onClick={() => setAsking(true)}>{t("devices.bulk.decommission")}</button>
        ))}
        <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={busy} onClick={onClear}>{t("devices.bulk.clear")}</button>
      </div>
      {asking && (
        <form className="ui-selection-form" onSubmit={(event) => {
          event.preventDefault();
          setAsking(false);
          void run("decommission", chosen, { reason: reason.trim() });
        }}>
          <label><span>{t("decommission.reasonLabel")}</span>
            <input value={reason} onChange={(event) => setReason(event.target.value)} minLength={5} maxLength={500} required autoComplete="off"
              placeholder={t("decommission.reasonPlaceholder")} /></label>
          <button type="submit" className="ui-button ui-button-danger ui-button-small" disabled={reason.trim().length < 5}>
            {t("devices.bulk.decommissionConfirm", { count: chosen.length })}
          </button>
          <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={() => setAsking(false)}>{t("customerForm.cancel")}</button>
        </form>
      )}
      {result && (
        <div className="ui-selection-result" role="status">
          {result.running
            ? t("devices.bulk.progress", { done: result.done, total: result.total })
            : t("devices.bulk.finished", { ok: result.total - result.failed.length, total: result.total })}
          {result.failed.length > 0 && (
            <ul>
              {result.failed.slice(0, 8).map((item) => <li key={item.name}><strong>{item.name}</strong>: {item.message}</li>)}
              {result.failed.length > 8 && <li>{t("devices.bulk.moreFailures", { count: result.failed.length - 8 })}</li>}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
