import { useTranslation } from "react-i18next";

export interface AclRule {
  protocol: "tcp" | "udp";
  port: number | string;
  src: string;
  action: "accept" | "drop";
  comment: string;
}

const EMPTY: AclRule = { protocol: "tcp", port: "", src: "", action: "accept", comment: "" };

/** Extra airOS firewall rules typed by the operator, in their order: accept or drop, TCP or UDP, one port, an IPv4 source (empty: everybody). */
export function AclRuleEditor({ rules, onChange, max, readOnly = false }: {
  rules: AclRule[]; onChange: (rules: AclRule[]) => void; max: number; readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const set = (index: number, patch: Partial<AclRule>) => onChange(rules.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)));
  const move = (index: number, step: number) => {
    const next = [...rules];
    [next[index], next[index + step]] = [next[index + step], next[index]];
    onChange(next);
  };
  return (
    <>
      {rules.length > 0 ? (
        <div className="ui-data-wrap">
          <table className="ui-data-table">
            <thead>
              <tr>
                {["action", "protocol", "port", "source", "comment"].map((key) => <th key={key}><span className="ui-th">{t(`acl.rules.columns.${key}`)}</span></th>)}
                <th />
              </tr>
            </thead>
            <tbody>
              {rules.map((rule, index) => (
                <tr key={index}>
                  <td>
                    <select value={rule.action} disabled={readOnly} aria-label={t("acl.rules.columns.action")}
                      onChange={(event) => set(index, { action: event.target.value as AclRule["action"] })}>
                      <option value="accept">{t("acl.rules.actions.accept")}</option>
                      <option value="drop">{t("acl.rules.actions.drop")}</option>
                    </select>
                  </td>
                  <td>
                    <select value={rule.protocol} disabled={readOnly} aria-label={t("acl.rules.columns.protocol")}
                      onChange={(event) => set(index, { protocol: event.target.value as AclRule["protocol"] })}>
                      <option value="tcp">TCP</option>
                      <option value="udp">UDP</option>
                    </select>
                  </td>
                  <td>
                    <input type="number" min={1} max={65535} value={rule.port} readOnly={readOnly} required aria-label={t("acl.rules.columns.port")}
                      onChange={(event) => set(index, { port: event.target.value })} />
                  </td>
                  <td>
                    <input className="ui-mono" value={rule.src} readOnly={readOnly} placeholder={t("acl.rules.anySource")} aria-label={t("acl.rules.columns.source")}
                      onChange={(event) => set(index, { src: event.target.value })} />
                  </td>
                  <td>
                    <input value={rule.comment} maxLength={32} readOnly={readOnly} aria-label={t("acl.rules.columns.comment")}
                      onChange={(event) => set(index, { comment: event.target.value })} />
                  </td>
                  <td className="ui-nowrap">
                    {!readOnly && (
                      <>
                        <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={index === 0} onClick={() => move(index, -1)}
                          aria-label={t("acl.rules.up")}>↑</button>
                        <button type="button" className="ui-button ui-button-ghost ui-button-small" disabled={index === rules.length - 1} onClick={() => move(index, 1)}
                          aria-label={t("acl.rules.down")}>↓</button>
                        <button type="button" className="ui-button ui-button-ghost ui-button-small" onClick={() => onChange(rules.filter((_, i) => i !== index))}>
                          {t("acl.rules.remove")}
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="ui-muted">{t("acl.rules.empty")}</p>
      )}
      {!readOnly && rules.length < max && (
        <div className="ui-row-actions">
          <button type="button" className="ui-button ui-button-ghost" onClick={() => onChange([...rules, { ...EMPTY }])}>{t("acl.rules.add")}</button>
        </div>
      )}
    </>
  );
}
