import { useState } from "react";
import { copyText } from "../lib/clipboard";
import { useTranslation } from "react-i18next";

function CopyBlock({ label, command }: { label: string; command: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState<boolean | null>(null);
  const copy = () => void copyText(command).then(setCopied);
  return (
    <div className="ui-enrollment-command">
      <small className="ui-muted ui-block">{label}</small>
      <pre className="ui-code-block">{command}</pre>
      <button type="button" className="ui-button ui-button-small" onClick={copy}>{copied ? t("enrollment.copied") : t("enrollment.copy")}</button>
      {copied === false && <small className="ui-key-hint ui-block">{t("app.copyFailed")}</small>}
    </div>
  );
}

/** One-shot agent install commands: RouterOS 7 (guided) and, when available, RouterOS 6.48/6.49 with the same token. */
export function EnrollmentCommand({ command, commandV6 }: { command: string; commandV6?: string | null }) {
  const { t } = useTranslation();
  return (
    <div className="ui-alert ui-alert-warning ui-enrollment">
      <strong>{t("enrollment.title")}</strong>
      <p>{t("enrollment.help")}</p>
      <CopyBlock label={t("enrollment.v7")} command={command} />
      {commandV6 && <CopyBlock label={t("enrollment.v6")} command={commandV6} />}
      <small className="ui-muted">{t("enrollment.once")}</small>
    </div>
  );
}
