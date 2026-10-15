import { useState } from "react";
import { useTranslation } from "react-i18next";

/** One-shot RouterOS command that installs the NSM agent; the token inside is shown only this once. */
export function EnrollmentCommand({ command }: { command: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard?.writeText(command).then(() => setCopied(true), () => setCopied(false));
  };
  return (
    <div className="ui-alert ui-alert-warning ui-enrollment">
      <strong>{t("enrollment.title")}</strong>
      <p>{t("enrollment.help")}</p>
      <pre className="ui-code-block">{command}</pre>
      <div className="ui-row-actions">
        <button type="button" className="ui-button ui-button-small" onClick={copy}>{copied ? t("enrollment.copied") : t("enrollment.copy")}</button>
        <small className="ui-muted">{t("enrollment.once")}</small>
      </div>
    </div>
  );
}
