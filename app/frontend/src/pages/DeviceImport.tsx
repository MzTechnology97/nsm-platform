import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, ApiError } from "../api/client";
import { BrandIcon, type BrandRef } from "../app/BrandIcon";
import { useFormError } from "./CustomerForm";

interface ImportRow {
  row: number;
  status: "ready" | "imported" | "already_exists" | "error";
  message: string;
  device_id: string | null;
  customer: string;
  site: string;
  vendor: string;
  device_type: string;
  name: string;
  management_ip: string | null;
  primary_mac: string | null;
  serial_number: string | null;
  brand: BrandRef;
}

interface ImportResult {
  filename: string;
  mode: "validate" | "import";
  summary: Record<string, number>;
  rows: ImportRow[];
}

const TONE: Record<ImportRow["status"], string> = { ready: "online", imported: "online", already_exists: "warning", error: "offline" };

/** CSV import of devices (administrators): a dry run first, then the import of the rows that are ready. */
export function DeviceImport() {
  const { t } = useTranslation();
  const errorText = useFormError();
  const client = useQueryClient();
  const info = useQuery({
    queryKey: ["devices", "import-info"],
    queryFn: () => api<{ max_rows: number; max_bytes: number; columns: string[]; required: string[]; template: string }>("/api/ui/v1/device-import"),
  });
  const run = useMutation({
    mutationFn: ({ file, mode }: { file: File; mode: "validate" | "import" }) => {
      const body = new FormData();
      body.append("csv_file", file);
      body.append("mode", mode);
      return api<ImportResult>("/api/ui/v1/device-import", { method: "POST", body });
    },
    onSuccess: (result) => {
      if (result.mode === "import") void client.invalidateQueries({ queryKey: ["devices"] });
    },
  });
  const [file, setFile] = useState<File | null>(null);
  const result = run.data;
  const validated = result?.mode === "validate" && result.filename === file?.name;
  const ready = validated ? result.summary.ready ?? 0 : 0;

  const submit = (mode: "validate" | "import") => (event?: FormEvent) => {
    event?.preventDefault();
    if (file) run.mutate({ file, mode });
  };

  return (
    <section className="ui-page">
      <nav className="ui-breadcrumbs" aria-label={t("device.breadcrumbs")}>
        <Link to="/devices">{t("nav.devices")}</Link>
        <span aria-hidden="true">›</span>
        <span>{t("deviceImport.title")}</span>
      </nav>
      <header className="ui-page-header">
        <h1>{t("deviceImport.title")}</h1>
        <p>{t("deviceImport.subtitle")}</p>
      </header>
      <section className="ui-panel ui-admin-section">
        <form className="ui-admin-form" onSubmit={submit("validate")}>
          <label className="ui-admin-wide"><span>{t("deviceImport.file")}</span>
            <input type="file" accept=".csv,text/csv" onChange={(event) => { setFile(event.target.files?.[0] ?? null); run.reset(); }} required /></label>
          <button type="submit" className="ui-button ui-button-ghost" disabled={!file || run.isPending}>{t("deviceImport.validate")}</button>
          <button type="button" className="ui-button" disabled={!file || run.isPending || ready === 0} onClick={() => submit("import")()}>
            {t("deviceImport.import", { count: ready })}
          </button>
          <a className="ui-button ui-button-ghost" href="/devices/import/template.csv">{t("deviceImport.template")}</a>
        </form>
        {info.data && (
          <p className="ui-muted">
            {t("deviceImport.rules", { rows: info.data.max_rows, size: Math.round(info.data.max_bytes / 1024) })}{" "}
            <span className="ui-mono">{info.data.columns.join(", ")}</span>
          </p>
        )}
        <p className="ui-muted">{t("deviceImport.noCredentials")}</p>
        {run.error && (
          <div className="ui-alert ui-alert-danger">
            {errorText(run.error, "deviceImport.errors")}
            {run.error instanceof ApiError && run.error.code === "file_invalid" && <small className="ui-block">{run.error.message}</small>}
          </div>
        )}
      </section>
      {result && (
        <section className="ui-panel ui-admin-section">
          <div className="ui-page-header-row">
            <h2>{result.mode === "import" ? t("deviceImport.resultImport") : t("deviceImport.resultValidate")}</h2>
            <span className="ui-muted">{result.filename}</span>
          </div>
          <div className="ui-chips">
            {(["ready", "imported", "already_exists", "error"] as const).filter((key) => (result.summary[key] ?? 0) > 0 || key === "error").map((key) => (
              <span key={key} className="ui-chip">{t(`deviceImport.status.${key}`)} <span className="ui-chip-count">{result.summary[key] ?? 0}</span></span>
            ))}
          </div>
          <div className="ui-data-wrap">
            <table className="ui-data-table">
              <thead><tr>{["row", "device", "customer", "address", "outcome"].map((key) => <th key={key}><span className="ui-th">{t(`deviceImport.columns.${key}`)}</span></th>)}</tr></thead>
              <tbody>
                {result.rows.map((row) => (
                  <tr key={row.row}>
                    <td className="ui-mono">{row.row}</td>
                    <td>
                      <span className="ui-with-brand">
                        <BrandIcon brand={row.brand} small />
                        {row.device_id ? (
                          <Link to="/devices/$deviceId" params={{ deviceId: row.device_id }} className="ui-cell-title">{row.name || "—"}</Link>
                        ) : (
                          <strong>{row.name || "—"}</strong>
                        )}
                      </span>
                      <small className="ui-muted ui-block">{[row.vendor, row.device_type].filter(Boolean).join(" · ")}</small>
                    </td>
                    <td>{row.customer}{row.site && <small className="ui-muted ui-block">{row.site}</small>}</td>
                    <td className="ui-mono">{row.management_ip ?? row.primary_mac ?? row.serial_number ?? "—"}</td>
                    <td>
                      <span className={`ui-status ui-status-${TONE[row.status]}`}>{t(`deviceImport.status.${row.status}`)}</span>
                      {row.status !== "ready" && row.status !== "imported" && <small className="ui-block">{row.message}</small>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </section>
  );
}
