import { Link } from "@tanstack/react-router";
import { type FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import {
  type SyslogRule, type SyslogRuleDraft, type SyslogRulesPage, useDeleteSyslogRule, useSaveSyslogRule, useSyslogRules, useTestSyslogRule,
} from "../api/syslogRules";
import { Loading } from "../app/States";

const VENDORS = ["mikrotik", "ubiquiti", "tp-link", "cambium", "mimosa", "cisco", "juniper", "fortinet", "huawei"];
const SEVERITY_TONE: Record<string, string> = {
  critical: "ui-severity ui-severity-critical",
  high: "ui-severity ui-severity-high",
  warning: "ui-severity ui-severity-warning",
  info: "ui-severity",
};

/** Syslog rules (SIEM-01): operator rules on top of the built-in access alerts, with a tester. */
export function SyslogRules() {
  const { t } = useTranslation();
  const query = useSyslogRules();
  const [editing, setEditing] = useState<SyslogRuleDraft | null>(null);
  // The form as it is being edited, so the tester tries the unsaved version.
  const [draft, setDraft] = useState<SyslogRuleDraft | null>(null);
  const data = query.data;
  const close = () => { setEditing(null); setDraft(null); };
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("syslogRules.title")}</h1>
          <p>{t("syslogRules.subtitle")}</p>
        </div>
        <div className="ui-actions">
          <Link to="/access" className="ui-button ui-button-ghost">{t("syslogRules.toAccess")}</Link>
          {data?.can_edit && !editing && (
            <button type="button" className="ui-button ui-button-primary" onClick={() => setEditing({ ...data.default })}>{t("syslogRules.new")}</button>
          )}
        </div>
      </header>
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : (
        <>
          {editing && <RuleEditor key={editing.id ?? `new-${editing.pattern}`} data={data} initial={editing} onChange={setDraft} onDone={close} />}
          <RuleList data={data} onEdit={(rule) => setEditing(rule)} />
          <Tester draft={editing ? draft : null} />
          <section className="ui-panel ui-section-gap">
            <h2>{t("syslogRules.builtinTitle")}</h2>
            <p className="ui-muted">{t("syslogRules.builtinHelp")}</p>
            <ul className="ui-cap-list">
              {data.builtin.map((rule) => (
                <li key={rule.key}>
                  <div><strong>{t(`syslogRules.builtin.${rule.key}`)}</strong><small className="ui-muted">{rule.description}</small></div>
                  <span className={SEVERITY_TONE[rule.level] ?? "ui-severity"}>{t(`syslogRules.levels.${rule.level}`)}</span>
                </li>
              ))}
            </ul>
            <h3>{t("syslogRules.patternsTitle")}</h3>
            <p className="ui-muted">{t("syslogRules.patternsHelp")}</p>
            <div className="ui-data-wrap">
              <table className="ui-data-table">
                <thead><tr>{["outcome", "service", "pattern", ""].map((key) => <th key={key}>{key && <span className="ui-th">{t(`syslogRules.patterns.${key}`)}</span>}</th>)}</tr></thead>
                <tbody>
                  {data.builtin_patterns.map((row) => (
                    <tr key={row.pattern}>
                      <td>{t(`syslogRules.outcomes.${row.outcome}`)}</td>
                      <td>{row.service || t("syslogRules.patterns.anyService")}</td>
                      <td className="ui-audit-details"><code title={row.pattern}>{row.pattern}</code></td>
                      <td>
                        {data.can_edit && (
                          <button type="button" className="ui-button ui-button-ghost ui-button-small"
                            onClick={() => { setEditing({ ...row.copy }); window.scrollTo({ top: 0, behavior: "smooth" }); }}>
                            {t("syslogRules.patterns.copy")}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </section>
  );
}

function RuleList({ data, onEdit }: { data: SyslogRulesPage; onEdit: (rule: SyslogRule) => void }) {
  const { t } = useTranslation();
  const remove = useDeleteSyslogRule();
  return (
    <section className="ui-panel ui-section-gap">
      <h2>{t("syslogRules.listTitle")}</h2>
      {remove.error && <div className="ui-alert ui-alert-danger">{(remove.error as Error).message}</div>}
      {data.rules.length ? (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead><tr>{["name", "match", "correlation", "level", "alerts", "actions"].map((key) => <th key={key}><span className="ui-th">{t(`syslogRules.columns.${key}`)}</span></th>)}</tr></thead>
            <tbody>
              {data.rules.map((rule) => (
                <tr key={rule.id} className={rule.enabled ? undefined : "ui-muted"}>
                  <td>
                    <strong>{rule.name}</strong>
                    <small className="ui-muted ui-block">{rule.enabled ? t(`syslogRules.actions.${rule.action}`) : t("syslogRules.disabled")}</small>
                  </td>
                  <td className="ui-audit-details">
                    <code title={rule.pattern}>{rule.pattern}</code>
                    <small className="ui-muted ui-block">{[rule.vendor || t("syslogRules.anyVendor"), t("syslogRules.maxSeverity", { level: rule.max_severity }), rule.topic].filter(Boolean).join(" · ")}</small>
                  </td>
                  <td>
                    {rule.count > 1 ? t("syslogRules.countIn", { count: rule.count, minutes: rule.window_minutes, group: t(`syslogRules.groups.${rule.group_by}`) }) : t("syslogRules.single")}
                    {rule.after_rule && <small className="ui-muted ui-block">{t("syslogRules.afterRule", { name: data.rules.find((r) => r.id === rule.after_rule)?.name ?? "?", minutes: rule.after_minutes })}</small>}
                  </td>
                  <td><span className={SEVERITY_TONE[rule.level]}>{t(`syslogRules.levels.${rule.level}`)}</span></td>
                  <td>{rule.alerts_24h ?? 0}</td>
                  <td>
                    {data.can_edit && (
                      <div className="ui-row-actions">
                        <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={() => onEdit(rule)}>{t("syslogRules.edit")}</button>
                        <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={remove.isPending}
                          onClick={() => window.confirm(t("syslogRules.confirmDelete", { name: rule.name })) && remove.mutate(rule.id)}>
                          {t("syslogRules.delete")}
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ui-empty">{t("syslogRules.none")}</div>
      )}
    </section>
  );
}

function RuleEditor({ data, initial, onChange, onDone }: {
  data: SyslogRulesPage; initial: SyslogRuleDraft; onChange: (draft: SyslogRuleDraft) => void; onDone: () => void;
}) {
  const { t } = useTranslation();
  const save = useSaveSyslogRule();
  const [form, setForm] = useState<SyslogRuleDraft>(initial);
  const [sources, setSources] = useState(initial.ignore_sources.join("\n"));
  useEffect(() => onChange({ ...form, ignore_sources: sources.split(/[\s,;]+/).filter(Boolean) }), [form, sources, onChange]);
  const set = <K extends keyof SyslogRuleDraft>(key: K, value: SyslogRuleDraft[K]) => setForm({ ...form, [key]: value });
  const error = save.error instanceof ApiError ? save.error.message : save.error ? (save.error as Error).message : null;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate({ ...form, ignore_sources: sources.split(/[\s,;]+/).filter(Boolean) }, { onSuccess: onDone });
  };
  return (
    <section className="ui-panel ui-section-gap">
      <h2>{initial.id ? t("syslogRules.editTitle") : t("syslogRules.newTitle")}</h2>
      {!initial.id && (
        <label className="ui-admin-form">
          <span>{t("syslogRules.template")}</span>
          <select value="" onChange={(event) => {
            const template = data.templates.find((item) => item.key === event.target.value);
            if (template) { setForm({ ...data.default, ...template }); setSources(""); }
          }}>
            <option value="">{t("syslogRules.templateNone")}</option>
            {data.templates.map((template) => <option key={template.key} value={template.key}>{template.name}</option>)}
          </select>
        </label>
      )}
      <form className="ui-admin-form ui-admin-stack" onSubmit={submit}>
        <label><span>{t("syslogRules.fields.name")}</span><input required maxLength={80} value={form.name} onChange={(event) => set("name", event.target.value)} /></label>
        <label>
          <span>{t("syslogRules.fields.pattern")}</span>
          <input required className="ui-mono" maxLength={300} value={form.pattern} onChange={(event) => set("pattern", event.target.value)} spellCheck={false}
            placeholder={"login failure for user (?P<user>\\S+) from (?P<ip>[0-9.]+)"} />
          <small className="ui-muted">{t("syslogRules.fields.patternHelp")}</small>
        </label>
        <label>
          <span>{t("syslogRules.fields.vendor")}</span>
          <input list="syslog-rule-vendors" value={form.vendor} onChange={(event) => set("vendor", event.target.value)} placeholder={t("syslogRules.anyVendor")} />
          <datalist id="syslog-rule-vendors">{VENDORS.map((vendor) => <option key={vendor} value={vendor} />)}</datalist>
        </label>
        <label>
          <span>{t("syslogRules.fields.maxSeverity")}</span>
          <select value={form.max_severity} onChange={(event) => set("max_severity", Number(event.target.value))}>
            {[0, 1, 2, 3, 4, 5, 6, 7].map((level) => <option key={level} value={level}>{t(`syslogRules.severities.${level}`)}</option>)}
          </select>
        </label>
        <label><span>{t("syslogRules.fields.topic")}</span><input maxLength={80} value={form.topic} onChange={(event) => set("topic", event.target.value)} placeholder="system" /></label>
        <label>
          <span>{t("syslogRules.fields.count")}</span>
          <input type="number" min={1} max={1000} value={form.count} onChange={(event) => set("count", Number(event.target.value))} />
          <small className="ui-muted">{t("syslogRules.fields.countHelp")}</small>
        </label>
        <label><span>{t("syslogRules.fields.window")}</span><input type="number" min={1} max={1440} value={form.window_minutes} onChange={(event) => set("window_minutes", Number(event.target.value))} /></label>
        <label>
          <span>{t("syslogRules.fields.groupBy")}</span>
          <select value={form.group_by} onChange={(event) => set("group_by", event.target.value as SyslogRuleDraft["group_by"])}>
            {data.groups.map((group) => <option key={group} value={group}>{t(`syslogRules.groups.${group}`)}</option>)}
          </select>
        </label>
        <label>
          <span>{t("syslogRules.fields.afterRule")}</span>
          <select value={form.after_rule} onChange={(event) => set("after_rule", event.target.value)}>
            <option value="">{t("syslogRules.fields.afterNone")}</option>
            {data.rules.filter((rule) => rule.id !== form.id).map((rule) => <option key={rule.id} value={rule.id}>{rule.name}</option>)}
          </select>
        </label>
        {form.after_rule && (
          <label><span>{t("syslogRules.fields.afterMinutes")}</span><input type="number" min={1} max={1440} value={form.after_minutes} onChange={(event) => set("after_minutes", Number(event.target.value))} /></label>
        )}
        <label>
          <span>{t("syslogRules.fields.ignore")}</span>
          <textarea rows={2} value={sources} onChange={(event) => setSources(event.target.value)} placeholder={"192.0.2.10\n198.51.100.0/24"} spellCheck={false} />
          <small className="ui-muted">{t("syslogRules.fields.ignoreHelp")}</small>
        </label>
        <label><span>{t("syslogRules.fields.throttle")}</span><input type="number" min={0} max={10080} value={form.throttle_minutes} onChange={(event) => set("throttle_minutes", Number(event.target.value))} /></label>
        <label>
          <span>{t("syslogRules.fields.level")}</span>
          <select value={form.level} onChange={(event) => set("level", event.target.value as SyslogRuleDraft["level"])}>
            {data.levels.map((level) => <option key={level} value={level}>{t(`syslogRules.levels.${level}`)}</option>)}
          </select>
        </label>
        <label>
          <span>{t("syslogRules.fields.action")}</span>
          <select value={form.action} onChange={(event) => set("action", event.target.value as SyslogRuleDraft["action"])}>
            {data.actions.map((action) => <option key={action} value={action}>{t(`syslogRules.actions.${action}`)}</option>)}
          </select>
        </label>
        <label className="ui-with-brand">
          <input type="checkbox" checked={form.enabled} onChange={(event) => set("enabled", event.target.checked)} />
          <span>{t("syslogRules.fields.enabled")}</span>
        </label>
        <div className="ui-row-actions">
          <button type="submit" className="ui-button ui-button-primary" disabled={save.isPending}>{t("syslogRules.save")}</button>
          <button type="button" className="ui-button ui-button-ghost" onClick={onDone}>{t("syslogRules.cancel")}</button>
        </div>
        {error && <small className="ui-backup-error">{error}</small>}
      </form>
    </section>
  );
}

function Tester({ draft }: { draft: SyslogRuleDraft | null }) {
  const { t } = useTranslation();
  const test = useTestSyslogRule();
  const [line, setLine] = useState({ message: "", vendor: "mikrotik", severity: 6, topics: "" });
  return (
    <section className="ui-panel ui-section-gap">
      <h2>{t("syslogRules.testerTitle")}</h2>
      <p className="ui-muted">{t("syslogRules.testerHelp")}</p>
      <form className="ui-admin-form ui-admin-stack" onSubmit={(event) => { event.preventDefault(); test.mutate({ ...line, draft: draft ?? undefined }); }}>
        <label>
          <span>{t("syslogRules.tester.message")}</span>
          <textarea rows={2} className="ui-mono" value={line.message} onChange={(event) => setLine({ ...line, message: event.target.value })} spellCheck={false}
            placeholder="login failure for user admin from 203.0.113.9 via winbox" />
        </label>
        <label>
          <span>{t("syslogRules.fields.vendor")}</span>
          <input list="syslog-rule-vendors" value={line.vendor} onChange={(event) => setLine({ ...line, vendor: event.target.value })} />
        </label>
        <label>
          <span>{t("syslogRules.tester.severity")}</span>
          <select value={line.severity} onChange={(event) => setLine({ ...line, severity: Number(event.target.value) })}>
            {[0, 1, 2, 3, 4, 5, 6, 7].map((level) => <option key={level} value={level}>{t(`syslogRules.severities.${level}`)}</option>)}
          </select>
        </label>
        <label><span>{t("syslogRules.fields.topic")}</span><input value={line.topics} onChange={(event) => setLine({ ...line, topics: event.target.value })} placeholder="system,info,account" /></label>
        <button type="submit" className="ui-button" disabled={test.isPending || !line.message.trim()}>{t("syslogRules.tester.run")}</button>
      </form>
      {test.error && <div className="ui-alert ui-alert-danger">{(test.error as Error).message}</div>}
      {test.data && (
        <p className="ui-muted ui-section-gap">
          <strong>{t("syslogRules.tester.builtin")}</strong>{" "}
          {test.data.builtin
            ? [t(`syslogRules.outcomes.${test.data.builtin.outcome}`), test.data.builtin.username && `user=${test.data.builtin.username}`,
               test.data.builtin.remote_ip && `ip=${test.data.builtin.remote_ip}`, test.data.builtin.service && `svc=${test.data.builtin.service}`].filter(Boolean).join(" · ")
            : t("syslogRules.tester.builtinNone")}
        </p>
      )}
      {test.data && (
        test.data.results.length ? (
          <ul className="ui-cap-list ui-section-gap">
            {test.data.results.map((result) => (
              <li key={`${result.id}-${result.draft}`}>
                <div>
                  <strong>{result.name || t("syslogRules.tester.draft")}{result.draft && result.name ? ` (${t("syslogRules.tester.draft")})` : ""}</strong>
                  {result.error ? <small className="ui-backup-error">{result.error}</small>
                    : result.matched ? (
                      <small className="ui-muted">
                        {Object.entries(result.fields ?? {}).map(([key, value]) => `${key}=${value}`).join(" · ") || t("syslogRules.tester.noFields")}
                        {" — "}{result.ignored ? t("syslogRules.tester.ignored") : result.fires_alone ? t("syslogRules.tester.firesAlone") : t("syslogRules.tester.needsCorrelation")}
                      </small>
                    ) : <small className="ui-muted">{t("syslogRules.tester.noMatch")}</small>}
                </div>
                <span className={result.matched && !result.ignored ? SEVERITY_TONE[result.level] ?? "ui-severity" : "ui-severity"}>
                  {result.matched ? t("syslogRules.tester.matched") : t("syslogRules.tester.notMatched")}
                </span>
              </li>
            ))}
          </ul>
        ) : <div className="ui-empty">{t("syslogRules.tester.noRules")}</div>
      )}
    </section>
  );
}
