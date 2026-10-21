import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { useDemoAction, useDemoAdmin } from "../api/admin";
import { AdminTabs } from "../app/AdminTabs";
import { Loading } from "../app/States";

export function AdminDemo() {
  const { t } = useTranslation();
  const query = useDemoAdmin();
  const seed = useDemoAction<object>("/seed");
  const clear = useDemoAction<{ confirm: string }>("/clear");
  const [confirm, setConfirm] = useState("");
  const count = query.data?.demo_customers ?? 0;
  const submitClear = (event: FormEvent) => {
    event.preventDefault();
    clear.mutate({ confirm }, { onSuccess: () => setConfirm("") });
  };
  return (
    <section className="ui-page">
      <header className="ui-page-header ui-page-header-row">
        <div>
          <h1>{t("admin.title")}</h1>
          <p>{t("demo.subtitle")}</p>
        </div>
      </header>
      <AdminTabs />
      {query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !query.data ? (
        <Loading />
      ) : (
        <div className="ui-two-columns">
          <section className="ui-panel ui-admin-section">
            <h2>{t("demo.dataset")}</h2>
            <p className="ui-muted">{t("demo.datasetHelp")}</p>
            <div className="ui-metrics">
              <div className="ui-metric"><span>{t("demo.customers")}</span><strong>{count}</strong><small>{t("demo.code")}</small></div>
            </div>
            {count ? (
              <p className="ui-muted">{t("demo.inUse")}</p>
            ) : (
              <button type="button" className="ui-button" disabled={seed.isPending} onClick={() => seed.mutate({})}>
                {seed.isPending ? t("demo.seeding") : t("demo.seed")}
              </button>
            )}
            {seed.isSuccess && <p className="ui-live">{t("demo.seeded")}</p>}
            {seed.error && <p className="ui-backup-error">{(seed.error as Error).message}</p>}
          </section>
          <section className="ui-panel ui-admin-section ui-danger-zone">
            <h2>{t("demo.clearTitle")}</h2>
            <p className="ui-muted">{t("demo.clearHelp")}</p>
            <form className="ui-admin-form ui-admin-stack" onSubmit={submitClear}>
              <label><span>{t("demo.typeDemo")}</span><input value={confirm} onChange={(event) => setConfirm(event.target.value)} autoComplete="off" required /></label>
              <button type="submit" className="ui-button ui-button-danger" disabled={!count || confirm.trim().toUpperCase() !== "DEMO" || clear.isPending}>
                {clear.isPending ? t("demo.clearing") : t("demo.clear")}
              </button>
              {clear.isSuccess && <small className="ui-live">{t("demo.cleared")}</small>}
              {clear.error && <small className="ui-backup-error">{(clear.error as Error).message}</small>}
            </form>
          </section>
        </div>
      )}
    </section>
  );
}
