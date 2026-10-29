import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../api/client";
import { formatDateTime, fromLocalInput, toLocalInput } from "../lib/format";

export interface FirmwareSchedule {
  at: string;
  by: string | null;
  state: "scheduled" | "checking" | "running" | "success" | "failed" | "cancelled";
  plan_id: string | null;
  target_version: string | null;
  error: string | null;
  started_at?: string | null;
  ended_at?: string | null;
}

const TONE: Record<string, string> = { success: "online", failed: "offline", cancelled: "offline" };

function message(error: unknown) {
  return error instanceof ApiError ? error.message : (error as Error | null)?.message;
}

/** Default proposal: the next 03:00 (local time of the browser). */
function nextNight(now = new Date()): string {
  const at = new Date(now);
  at.setHours(3, 0, 0, 0);
  if (at.getTime() <= now.getTime() + 60_000) at.setDate(at.getDate() + 1);
  return toLocalInput(at.toISOString());
}

/** State of a schedule in one line (worklist and device tab). */
export function ScheduleBadge({ schedule }: { schedule: FirmwareSchedule }) {
  const { t, i18n } = useTranslation();
  return (
    <small className="ui-block">
      <span className={`ui-status ui-status-${TONE[schedule.state] ?? "warning"}`}>{t(`firmwareSchedule.states.${schedule.state}`)}</span>{" "}
      {t("firmwareSchedule.at", { when: formatDateTime(schedule.at, i18n.language) })}
      {schedule.error && <span className="ui-backup-error ui-block">{schedule.error}</span>}
    </small>
  );
}

/** Device Firmware tab: schedule the RouterOS upgrade at a time; at that time every phase runs without further confirmations. */
export function FirmwareSchedulePanel({ deviceId, schedule, canExecute, target }: {
  deviceId: string; schedule: FirmwareSchedule | null | undefined; canExecute: boolean; target: string | null | undefined;
}) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const [when, setWhen] = useState(nextNight);
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["device", deviceId, "firmware-plan"] });
    void client.invalidateQueries({ queryKey: ["firmware"] });
  };
  const save = useMutation({
    mutationFn: (at: string) => api(`/api/ui/v1/devices/${encodeURIComponent(deviceId)}/firmware-upgrade/schedule`, { method: "POST", body: JSON.stringify({ at }) }),
    onSuccess: refresh,
  });
  const cancel = useMutation({
    mutationFn: () => api(`/api/ui/v1/devices/${encodeURIComponent(deviceId)}/firmware-upgrade/schedule/cancel`, { method: "POST", body: "{}" }),
    onSuccess: refresh,
  });
  const active = schedule && ["scheduled", "checking", "running"].includes(schedule.state);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const at = fromLocalInput(when);
    if (at && window.confirm(t("firmwareSchedule.confirm", { when: formatDateTime(at, i18n.language), target: target ?? "—" }))) save.mutate(at);
  };
  return (
    <section className="ui-panel ui-admin-section">
      <h2>{t("firmwareSchedule.title")}</h2>
      <p className="ui-muted">{t("firmwareSchedule.help")}</p>
      {schedule && <ScheduleBadge schedule={schedule} />}
      {canExecute && (
        <form className="ui-row-actions" onSubmit={submit}>
          <label><span className="ui-muted">{t("firmwareSchedule.when")}</span>{" "}
            <input type="datetime-local" value={when} onChange={(event) => setWhen(event.target.value)} required disabled={schedule?.state === "running"} /></label>
          <button type="submit" className="ui-button ui-button-primary" disabled={save.isPending || schedule?.state === "running"}>
            {active ? t("firmwareSchedule.move") : t("firmwareSchedule.schedule")}
          </button>
          {active && schedule?.state !== "running" && (
            <button type="button" className="ui-button ui-button-ghost" disabled={cancel.isPending} onClick={() => cancel.mutate()}>{t("firmwareSchedule.cancel")}</button>
          )}
        </form>
      )}
      {(save.error || cancel.error) && <small className="ui-backup-error ui-block">{message(save.error ?? cancel.error)}</small>}
    </section>
  );
}

/** Firmware worklist: schedule every selected MikroTik at the same time. */
export function ScheduleBar({ selected, onDone }: { selected: string[]; onDone: () => void }) {
  const { t, i18n } = useTranslation();
  const client = useQueryClient();
  const [when, setWhen] = useState(nextNight);
  const run = useMutation({
    mutationFn: (at: string) => api<{ scheduled: number; skipped: { id: string; name: string; reason: string }[] }>("/api/ui/v1/firmware/schedule",
      { method: "POST", body: JSON.stringify({ at, device_ids: selected }) }),
    onSuccess: () => { void client.invalidateQueries({ queryKey: ["firmware"] }); onDone(); },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const at = fromLocalInput(when);
    if (at && window.confirm(t("firmwareSchedule.confirmMany", { count: selected.length, when: formatDateTime(at, i18n.language) }))) run.mutate(at);
  };
  if (!selected.length && !run.data) return null;
  return (
    <form className="ui-filter-bar" onSubmit={submit}>
      {selected.length > 0 && (
        <>
          <strong>{t("firmwareSchedule.selected", { count: selected.length })}</strong>
          <input type="datetime-local" value={when} onChange={(event) => setWhen(event.target.value)} required aria-label={t("firmwareSchedule.when")} />
          <button type="submit" className="ui-button ui-button-primary" disabled={run.isPending}>{t("firmwareSchedule.scheduleMany")}</button>
        </>
      )}
      {run.data && (
        <small className={run.data.skipped.length ? "ui-text-warning" : "ui-muted"}>
          {t("firmwareSchedule.done", { count: run.data.scheduled })}
          {run.data.skipped.length > 0 && ` ${t("firmwareSchedule.skipped", { names: run.data.skipped.map((s) => s.name).join(", ") })}`}
        </small>
      )}
      {run.error && <small className="ui-backup-error">{message(run.error)}</small>}
    </form>
  );
}
