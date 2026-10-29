import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { BackupPolicyList } from "../app/BackupPolicyList";

/** Backup policies: their own page, so they never end up below the device list. */
export function BackupPolicies() {
  const { t } = useTranslation();
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("backupPolicies.title")}</h1>
          <p>{t("backupPolicies.subtitle")}</p>
        </div>
        <Link to="/backups" className="ui-button ui-button-ghost">{t("backupPolicies.back")}</Link>
      </header>
      <BackupPolicyList />
    </section>
  );
}
