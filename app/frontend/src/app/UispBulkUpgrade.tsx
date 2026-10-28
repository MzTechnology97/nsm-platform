import { useState } from "react";
import { useTranslation } from "react-i18next";

import { type FirmwareRow, type UispBulkResult, useUispBulkUpgrade } from "../api/operations";
import { useFormError } from "../pages/CustomerForm";

/** Rows of the firmware worklist that can join a bulk UISP upgrade. */
export const uispSelectable = (row: FirmwareRow) => Boolean(row.can_uisp_upgrade) && row.uisp_upgrade !== "requested";

/** Outcome of the last bulk request for one row (started / skipped with the reason). */
export function UispBulkOutcome({ result, id }: { result?: UispBulkResult; id: string }) {
  const { t } = useTranslation();
  const started = result?.started.find((entry) => entry.id === id);
  const skipped = result?.skipped.find((entry) => entry.id === id);
  if (started) return <small className="ui-muted ui-block">{t("firmware.uispBulk.started", { version: started.version })}</small>;
  if (skipped) return <small className="ui-backup-error ui-block">{t(`firmware.uispBulk.skipped.${skipped.code}`, { defaultValue: skipped.message })}</small>;
  return null;
}

/**
 * Action bar of the firmware worklist: upgrades the selected Ubiquiti devices through UISP in one UISP task.
 * Target: each device's recommended version, or one version for all; typed confirmation "AGGIORNA <n>".
 */
export function UispBulkUpgrade({ rows, selected, max, onDone }: {
  rows: FirmwareRow[];
  selected: string[];
  max: number;
  onDone: (result: UispBulkResult) => void;
}) {
  const { t } = useTranslation();
  const run = useUispBulkUpgrade();
  const errorText = useFormError();
  const [target, setTarget] = useState("recommended");
  const [maintenance, setMaintenance] = useState(false);
  const [confirm, setConfirm] = useState("");
  const count = selected.length;
  const phrase = `AGGIORNA ${count}`;
  const versions = [...new Set(rows.filter((row) => selected.includes(row.id) && row.recommended).map((row) => row.recommended as string))]
    .sort((a, b) => b.localeCompare(a, undefined, { numeric: true }));
  const choice = target === "recommended" || versions.includes(target) ? target : "recommended";
  const result = run.data;
  const submit = () => run.mutate({ device_ids: selected, target: choice, maintenance_window: maintenance, confirm: confirm.trim() }, {
    onSuccess: (data) => { setConfirm(""); onDone(data); },
  });

  return (
    <>
      {count > 0 && (
        <div className="ui-row-actions ui-policy-actions" role="group" aria-label={t("firmware.uispBulk.label")}>
          <small className="ui-muted">{t("firmware.uispBulk.selected", { count, max })}</small>
          <select value={choice} onChange={(event) => setTarget(event.target.value)} aria-label={t("firmware.uispBulk.target")}>
            <option value="recommended">{t("firmware.uispBulk.recommended")}</option>
            {versions.map((version) => <option key={version} value={version}>{t("firmware.uispBulk.version", { version })}</option>)}
          </select>
          <label className="ui-with-brand">
            <input type="checkbox" checked={maintenance} onChange={(event) => setMaintenance(event.target.checked)} /> <span>{t("firmware.uispBulk.maintenance")}</span>
          </label>
          <input value={confirm} onChange={(event) => setConfirm(event.target.value)} placeholder={phrase} aria-label={t("firmware.uispBulk.confirm", { phrase })} autoComplete="off" />
          <button type="button" className="ui-button ui-button-danger" disabled={count > max || confirm.trim() !== phrase || run.isPending} onClick={submit}>
            {t("firmware.uispBulk.run", { count })}
          </button>
        </div>
      )}
      {count > 0 && <small className="ui-muted ui-block">{t("firmware.uispBulk.help", { phrase })}</small>}
      {count > max && <div className="ui-alert ui-alert-warning">{t("firmware.uispBulk.tooMany", { max })}</div>}
      {result && (
        <div className={result.skipped.length ? "ui-alert ui-alert-warning" : "ui-alert ui-alert-success"}>
          <p>{t(result.maintenance_window ? "firmware.uispBulk.doneMaintenance" : "firmware.uispBulk.done", { count: result.started.length })}</p>
          {result.skipped.length > 0 && (
            <ul>
              {result.skipped.map((entry) => (
                <li key={entry.id}>{entry.name ?? entry.id}: {t(`firmware.uispBulk.skipped.${entry.code}`, { defaultValue: entry.message })}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {run.error && <div className="ui-alert ui-alert-danger">{errorText(run.error, "firmware.uispBulk.errors")}</div>}
    </>
  );
}
