import { useTranslation } from "react-i18next";

/** One manufacturer's remote-syslog setup, rendered by the server with NSM's address and the device key. */
export interface SyslogGuide {
  key: string;
  label: string;
  text: string;
  code: string;
  key_in_code: boolean;
}

/**
 * Manual syslog setup per manufacturer. The text is translated (``syslogGuides.<key>``, with ``{{target}}``);
 * commands come from the server. Without the key in the commands, the device name must carry it.
 */
export function SyslogGuides({ guides, target, deviceKey }: { guides: SyslogGuide[]; target: string; deviceKey: string | null }) {
  const { t } = useTranslation();
  const key = deviceKey ?? t("syslogGuides.keyPlaceholder");
  return (
    <div className="ui-syslog-guides">
      {guides.map((guide) => (
        <article key={guide.key} className="ui-syslog-guide" id={`syslog-${guide.key}`}>
          <h3>{guide.label}</h3>
          <p>{t(`syslogGuides.${guide.key}`, { target, defaultValue: guide.text })}</p>
          {guide.code && <pre className="ui-code-block">{guide.code}</pre>}
          {!guide.key_in_code && <p className="ui-muted">{t("syslogGuides.keyHint", { key })}</p>}
        </article>
      ))}
    </div>
  );
}
