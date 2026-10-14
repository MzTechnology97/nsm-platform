import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { can, useSession } from "../api/session";
import { Icon } from "../app/Icon";
import { SECTIONS } from "../app/sections";

export function Home() {
  const { t } = useTranslation();
  const { data } = useSession();
  const visible = SECTIONS.filter((section) => !section.permission || can(data, section.permission));
  return (
    <section className="ui-page">
      <header className="ui-page-header">
        <h1>{t("home.title")}</h1>
        <p>{t("home.intro")}</p>
      </header>
      <div className="ui-card-grid">
        {visible.map((section) => {
          const body = (
            <>
              <Icon name={section.icon} size={22} />
              <strong>{t(`nav.${section.key}`)}</strong>
              <small className={section.route ? "ui-tag ui-tag-new" : "ui-tag"}>{section.route ? t("home.migrated") : t("home.classic")}</small>
            </>
          );
          return section.route ? (
            <Link key={section.key} to={section.route} className="ui-card ui-card-link">
              {body}
            </Link>
          ) : (
            <a key={section.key} href={section.classic} className="ui-card ui-card-link">
              {body}
            </a>
          );
        })}
      </div>
    </section>
  );
}
