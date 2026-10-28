import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { type ArtifactFile, type DiffRow, useBackupArtifact } from "../api/backups";
import { ApiError } from "../api/client";
import { formatBytes, formatDateTime } from "../lib/format";
import { rscSections, tokenizeRsc } from "../lib/rsc";
import { Loading } from "../app/States";

const SIGN = { added: "+", removed: "−", context: " " } as const;

/** A RouterOS text export, read-only, optionally diffed with an older export of the same device. */
export function DeviceBackupArtifact() {
  const { t, i18n } = useTranslation();
  const { deviceId, artifactId } = useParams({ from: "/devices/$deviceId/backups/artifacts/$artifactId" });
  const against = useSearch({ from: "/devices/$deviceId/backups/artifacts/$artifactId" }).against ?? "";
  const navigate = useNavigate();
  const query = useBackupArtifact(deviceId, artifactId, against);
  const [filter, setFilter] = useState("");
  const [changesOnly, setChangesOnly] = useState(true);
  const data = query.data;

  const lines = useMemo(() => {
    const all = (data?.lines ?? []).map((text, index) => ({ number: index + 1, text }));
    const needle = filter.trim().toLowerCase();
    return needle ? all.filter((line) => line.text.toLowerCase().includes(needle)) : all;
  }, [data, filter]);
  const sections = useMemo(() => rscSections(data?.lines ?? []), [data]);
  const jump = (line: string) => document.getElementById(`rsc-L${line}`)?.scrollIntoView({ block: "start", behavior: "smooth" });
  const rows = useMemo(() => {
    const all = data?.diff?.rows ?? [];
    return changesOnly ? withContext(all, 3) : all;
  }, [data, changesOnly]);

  const compare = (value: string) =>
    navigate({ to: "/devices/$deviceId/backups/artifacts/$artifactId", params: { deviceId, artifactId }, search: value ? { against: value } : {} });
  const back = (
    <Link to="/devices/$deviceId/backups" params={{ deviceId }} className="ui-button ui-button-ghost">
      {t("backupArtifact.back")}
    </Link>
  );

  if (query.error) {
    const error = query.error instanceof ApiError ? query.error : null;
    return (
      <div className="ui-syslog">
        <div className="ui-alert ui-alert-danger">
          {error?.code ? t(`backupArtifact.errors.${error.code}`, { defaultValue: error.message }) : (query.error as Error).message}
        </div>
        <div className="ui-actions">
          {back}
          {against && <button type="button" className="ui-button ui-button-ghost" onClick={() => compare("")}>{t("backupArtifact.closeDiff")}</button>}
        </div>
      </div>
    );
  }
  if (!data) return <Loading />;
  const { artifact, diff } = data;

  return (
    <div className="ui-syslog">
      <section className="ui-panel">
        <div className="ui-panel-header">
          <div>
            <h2 className="ui-mono ui-break">{artifact.filename}</h2>
            <p className="ui-muted">
              {t("backupArtifact.meta", {
                when: formatDateTime(artifact.created_at, i18n.language),
                size: formatBytes(artifact.size, i18n.language),
                count: data.lines.length,
              })}
            </p>
            {artifact.sha256 && <p className="ui-muted"><code className="ui-break">SHA-256 {artifact.sha256}</code></p>}
          </div>
          <div className="ui-actions">
            {back}
            <a className="ui-button ui-button-ghost" href={data.links.download}>{t("backups.actions.download")}</a>
            <Link to="/devices/$deviceId/backups/artifacts/$artifactId/restore-test" params={{ deviceId, artifactId }} className="ui-button ui-button-ghost">
              {t("backups.actions.restore")}
            </Link>
          </div>
        </div>
        <div className="ui-filter-bar">
          <label className="ui-rsc-compare">
            <span>{t("backupArtifact.compareWith")}</span>
            <select value={against} onChange={(event) => compare(event.target.value)}>
              <option value="">{t("backupArtifact.noCompare")}</option>
              {data.candidates.map((candidate) => (
                <option key={candidate.id} value={candidate.id}>{label(candidate, i18n.language)}</option>
              ))}
            </select>
          </label>
          {!data.candidates.length && <small className="ui-muted">{t("backupArtifact.noCandidates")}</small>}
        </div>
      </section>

      {diff && data.against ? (
        <section className="ui-panel">
          <div className="ui-panel-header">
            <div>
              <h2>{t("backupArtifact.diffTitle")}</h2>
              <p className="ui-muted">
                {t("backupArtifact.diffHelp", { before: label(data.against, i18n.language), after: label(artifact, i18n.language) })}
              </p>
            </div>
            <div className="ui-rsc-summary">
              <span className="ui-rsc-added">{t("backupArtifact.added", { count: diff.summary.added })}</span>
              <span className="ui-rsc-removed">{t("backupArtifact.removed", { count: diff.summary.removed })}</span>
              <span className="ui-muted">{t("backupArtifact.unchanged", { count: diff.summary.unchanged })}</span>
            </div>
          </div>
          <div className="ui-filter-bar">
            <label className="ui-with-brand">
              <input type="checkbox" checked={changesOnly} onChange={(event) => setChangesOnly(event.target.checked)} />
              {t("backupArtifact.changesOnly")}
            </label>
            <button type="button" className="ui-button ui-button-ghost" onClick={() => compare("")}>{t("backupArtifact.closeDiff")}</button>
          </div>
          {diff.summary.truncated && <div className="ui-alert ui-alert-warning">{t("backupArtifact.truncated")}</div>}
          {diff.summary.added + diff.summary.removed === 0 ? (
            <div className="ui-empty">{t("backupArtifact.identical")}</div>
          ) : (
            <div className="ui-rsc ui-rsc-diff" role="table" aria-label={t("backupArtifact.diffTitle")}>
              {rows.map((row, index) =>
                row === null ? (
                  <div key={`gap-${index}`} className="ui-rsc-gap" role="row"><span role="cell">⋯</span></div>
                ) : (
                  <div key={index} className={`ui-rsc-line is-${row.kind}`} role="row">
                    <span className="ui-rsc-no" role="cell">{row.old ?? ""}</span>
                    <span className="ui-rsc-no" role="cell">{row.new ?? ""}</span>
                    <span className="ui-rsc-sign" role="cell" aria-label={t(`backupArtifact.kinds.${row.kind}`)}>{SIGN[row.kind]}</span>
                    <span className="ui-rsc-text" role="cell"><RscLine text={row.text} /></span>
                  </div>
                ),
              )}
            </div>
          )}
        </section>
      ) : (
        <section className="ui-panel">
          <div className="ui-panel-header">
            <div>
              <h2>{t("backupArtifact.contentTitle")}</h2>
              <p className="ui-muted">{t(data.artifact_type === "tr069_parameters" ? "backupArtifact.contentHelpTr069" : "backupArtifact.contentHelp")}</p>
            </div>
          </div>
          <div className="ui-filter-bar">
            <input type="search" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder={t("backupArtifact.filter")} aria-label={t("backupArtifact.filter")} />
            {filter && <small className="ui-muted">{t("backupArtifact.matches", { count: lines.length })}</small>}
            {sections.length > 1 && (
              <label className="ui-rsc-compare">
                <span>{t("backupArtifact.sections", { count: sections.length })}</span>
                <select value="" disabled={Boolean(filter)} onChange={(event) => jump(event.target.value)} title={filter ? t("backupArtifact.sectionsFiltered") : undefined}>
                  <option value="">{t("backupArtifact.jumpTo")}</option>
                  {sections.map((section) => (
                    <option key={section.line} value={section.line}>{t("backupArtifact.sectionOption", { path: section.path, count: section.size })}</option>
                  ))}
                </select>
              </label>
            )}
          </div>
          {lines.length ? (
            <div className="ui-rsc">
              {lines.map((line) => (
                <div key={line.number} id={`rsc-L${line.number}`} className="ui-rsc-line">
                  <span className="ui-rsc-no">{line.number}</span>
                  <span className="ui-rsc-text"><RscLine text={line.text} /></span>
                </div>
              ))}
            </div>
          ) : (
            <div className="ui-empty">{filter ? t("backupArtifact.noMatch") : t("backupArtifact.empty")}</div>
          )}
        </section>
      )}
    </div>
  );
}

/** One export line with RouterOS syntax colours (paths, commands, keys, values, strings, comments). */
function RscLine({ text }: { text: string }) {
  return (
    <>
      {tokenizeRsc(text).map((token, index) =>
        token.kind === "text" ? token.text : <span key={index} className={`ui-rsc-${token.kind}`}>{token.text}</span>,
      )}
    </>
  );
}

function label(file: ArtifactFile, locale: string): string {
  return `${formatDateTime(file.created_at ?? file.run_started_at, locale)} · ${file.filename}`;
}

/** Changed rows plus ``size`` rows of context around each change; ``null`` marks a skipped stretch. */
export function withContext(rows: DiffRow[], size: number): (DiffRow | null)[] {
  const keep = new Array<boolean>(rows.length).fill(false);
  rows.forEach((row, index) => {
    if (row.kind === "context") return;
    for (let i = Math.max(0, index - size); i <= Math.min(rows.length - 1, index + size); i += 1) keep[i] = true;
  });
  const out: (DiffRow | null)[] = [];
  rows.forEach((row, index) => {
    if (keep[index]) out.push(row);
    else if (out.length === 0 || out[out.length - 1] !== null) out.push(null);
  });
  return out;
}
