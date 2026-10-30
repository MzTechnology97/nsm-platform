import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../api/client";
import { formatDateTime, fromLocalInput, toLocalInput } from "../lib/format";
import { usePaging } from "../lib/paging";
import { Pager } from "./Pager";

interface Row { id: string; name: string; customer: string | null }
interface Candidate extends Row { netmode: string | null }
interface Problem extends Row { status: string; error: string | null; batch: string }
interface Batch { batch: string; at: string; hours: number; by: string | null; services: string[]; counts: Record<string, number> }
interface Fleet {
  candidates: Candidate[];
  problems: Problem[];
  batches: Batch[];
  counts: { candidate: number; applied: number; active: number; bridge: number; own_rules: number };
  services: string[];
  default_services: string[];
  max_bulk: number;
  default_hours: number;
  max_hours: number;
  can_schedule: boolean;
}

function message(error: unknown) {
  return error instanceof ApiError ? error.message : (error as Error | null)?.message;
}

/** Tomorrow at 01:00 in the browser's timezone, as a ``datetime-local`` value. */
function nextNight() {
  const date = new Date();
  date.setDate(date.getDate() + 1);
  date.setHours(1, 0, 0, 0);
  return toLocalInput(date.toISOString());
}

/** Exposure page: firewall rules on many airOS CPEs without active rules, applied by the worker in a nightly window. */
export function UispFirewallBulkPanel({ customer, toolbar }: { customer?: string; toolbar?: ReactNode }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["security", "uisp-firewall", customer ?? ""], queryFn: () => api<Fleet>(`/api/ui/v1/security/uisp-firewall${customer ? `?customer=${customer}` : ""}`) });
  const [chosen, setChosen] = useState<string[]>([]);
  const [picked, setPicked] = useState<string[] | null>(null);
  const [when, setWhen] = useState(nextNight);
  const [hours, setHours] = useState<number | null>(null);
  const [typed, setTyped] = useState("");
  const [text, setText] = useState("");
  const paging = usePaging(`${text}|${customer ?? ""}`);
  const refresh = () => void client.invalidateQueries({ queryKey: ["security", "uisp-firewall"] });
  const data = query.data;
  const services = picked ?? data?.default_services ?? [];
  const windowHours = hours ?? data?.default_hours ?? 4;
  const schedule = useMutation({
    mutationFn: () => api<{ queued: number; skipped: { id: string; name: string; reason: string }[] }>("/api/ui/v1/security/uisp-firewall/schedule", {
      method: "POST", body: JSON.stringify({ device_ids: chosen, confirmation: typed, at: fromLocalInput(when), hours: windowHours, services }),
    }),
    onSuccess: () => { setChosen([]); setTyped(""); refresh(); },
  });
  const cancel = useMutation({
    mutationFn: (batch: string) => api("/api/ui/v1/security/uisp-firewall/cancel", { method: "POST", body: JSON.stringify({ batch }) }),
    onSuccess: refresh,
  });
  if (!data) return null;
  const toggleService = (service: string) => setPicked(services.includes(service) ? services.filter((s) => s !== service) : data.services.filter((s) => s === service || services.includes(s)));
  const toggle = (rowId: string) => setChosen(chosen.includes(rowId) ? chosen.filter((value) => value !== rowId) : [...chosen, rowId]);
  const needle = text.trim().toLowerCase();
  const visible = data.candidates.filter((row) => !needle || `${row.name} ${row.customer ?? ""}`.toLowerCase().includes(needle));
  const all = visible.slice(0, data.max_bulk).map((row) => row.id);
  const submit = (event: FormEvent) => { event.preventDefault(); schedule.mutate(); };
  return (
    <section className="ui-panel ui-admin-section">
      <p className="ui-muted">{t("uispFirewallBulk.hint")}</p>
      <div className="ui-metrics">
        {(["candidate", "applied", "active", "own_rules", "bridge"] as const).map((name) => (
          <div key={name} className={`ui-metric${name === "candidate" && data.counts.candidate > 0 ? " ui-metric-warning" : ""}`}>
            <span>{t(`uispFirewallBulk.metrics.${name}`)}</span>
            <strong>{data.counts[name]}</strong>
          </div>
        ))}
      </div>

      {data.batches.length > 0 && (
        <>
          <h3>{t("uispFirewallBulk.requests")}</h3>
          <div className="ui-data-wrap">
            <table className="ui-data-table">
              <thead>
                <tr>{["start", "window", "by", "progress", "actions"].map((name) => <th key={name}><span className="ui-th">{t(`uispFirewallBulk.batch.${name}`)}</span></th>)}</tr>
              </thead>
              <tbody>
                {data.batches.map((batch) => (
                  <tr key={batch.batch}>
                    <td>{formatDateTime(batch.at, i18n.language)}</td>
                    <td>{t("uispFirewallBulk.batch.hours", { count: batch.hours })}</td>
                    <td>{batch.by ?? "—"}</td>
                    <td>{Object.entries(batch.counts).map(([status, count]) => `${t(`uispFirewallBulk.status.${status}`)}: ${count}`).join(" · ")}</td>
                    <td>{data.can_schedule && (batch.counts.scheduled ?? 0) > 0 && (
                      <button type="button" className="ui-button" disabled={cancel.isPending} onClick={() => cancel.mutate(batch.batch)}>{t("uispFirewallBulk.cancel")}</button>
                    )}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {cancel.error && <small className="ui-backup-error">{message(cancel.error)}</small>}

      {data.problems.length > 0 && (
        <details>
          <summary>{t("uispFirewallBulk.problems", { count: data.problems.length })}</summary>
          <ul>
            {data.problems.map((row) => (
              <li key={row.id}>
                <Link to="/devices/$deviceId/exposure" params={{ deviceId: row.id }}>{row.name}</Link>
                {" · "}{t(`uispFirewallBulk.status.${row.status}`)}{row.error ? `: ${row.error}` : ""}
              </li>
            ))}
          </ul>
        </details>
      )}

      <div className="ui-filter-bar">
        <input type="search" value={text} onChange={(event) => setText(event.target.value)} placeholder={t("exposure.search")} aria-label={t("exposure.search")} autoComplete="off" />
        {toolbar}
      </div>
      {visible.length === 0 ? (
        <div className="ui-empty">{t("uispFirewallBulk.none")}</div>
      ) : data.can_schedule && (
        <>
          <label>
            <input type="checkbox" checked={all.every((rowId) => chosen.includes(rowId))} onChange={(event) => setChosen(event.target.checked ? all : [])} />{" "}
            {t("uispFirewallBulk.all", { count: all.length })}
          </label>
          <details>
            <summary>{t("uispFirewallBulk.list", { count: visible.length })}</summary>
            <div className="ui-data-wrap">
              <table className="ui-data-table">
                <thead>
                  <tr>
                    <th />
                    {["device", "customer", "mode"].map((name) => <th key={name}><span className="ui-th">{t(`uispFirewallBulk.columns.${name}`)}</span></th>)}
                  </tr>
                </thead>
                <tbody>
                  {visible.slice(paging.offset, paging.offset + paging.size).map((row) => (
                    <tr key={row.id}>
                      <td><input type="checkbox" checked={chosen.includes(row.id)} onChange={() => toggle(row.id)} aria-label={row.name} /></td>
                      <td><Link to="/devices/$deviceId/exposure" params={{ deviceId: row.id }}>{row.name}</Link></td>
                      <td>{row.customer ?? "—"}</td>
                      <td>{row.netmode ?? t("uispFirewallBulk.modeUnknown")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager total={visible.length} paging={paging} />
          </details>
        </>
      )}

      {data.can_schedule && chosen.length > 0 && (
        <form className="ui-admin-form ui-admin-stack" onSubmit={submit}>
          <div className="ui-alert ui-alert-warning">{t("uispFirewallBulk.reboot", { count: chosen.length })}</div>
          <fieldset className="ui-row-actions">
            <legend>{t("uispFirewall.services")}</legend>
            {data.services.map((service) => (
              <label key={service}>
                <input type="checkbox" checked={services.includes(service)} disabled={services.length === 1 && services.includes(service)} onChange={() => toggleService(service)} />{" "}
                {t(`uispFirewall.service.${service}`)}
              </label>
            ))}
          </fieldset>
          <label>
            <span>{t("uispFirewallBulk.start")}</span>
            <input type="datetime-local" value={when} onChange={(event) => setWhen(event.target.value)} required />
          </label>
          <label>
            <span>{t("uispFirewallBulk.hours", { max: data.max_hours })}</span>
            <input type="number" min={1} max={data.max_hours} value={windowHours} onChange={(event) => setHours(Number(event.target.value))} required />
          </label>
          <p className="ui-muted">{t("uispFirewallBulk.estimate", { count: Math.ceil(chosen.length / (Math.max(windowHours, 1) * 60)) })}</p>
          <label>
            <span>{t("uispFirewallBulk.confirm", { count: chosen.length })}</span>
            <input value={typed} onChange={(event) => setTyped(event.target.value)} autoComplete="off" inputMode="numeric" />
          </label>
          <button type="submit" className="ui-button" disabled={schedule.isPending || typed.trim() !== String(chosen.length)}>{t("uispFirewallBulk.schedule", { count: chosen.length })}</button>
          {schedule.error && <small className="ui-backup-error">{message(schedule.error)}</small>}
        </form>
      )}
      {schedule.data && (
        <p className="ui-live">
          {t("uispFirewallBulk.queued", { count: schedule.data.queued })}
          {schedule.data.skipped.length > 0 ? ` ${t("uispFirewallBulk.skipped", { count: schedule.data.skipped.length })}` : ""}
        </p>
      )}
    </section>
  );
}
