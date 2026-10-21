import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { type FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useGlobalSearch } from "../api/personal";
import { BrandIcon } from "../app/BrandIcon";
import { Icon } from "../app/Icon";
import { Loading } from "../app/States";

/** Global search: devices (with vendor icon), customers and sites, by name, MAC, serial, IP or address. */
export function Search() {
  const { t } = useTranslation();
  const q = useSearch({ from: "/search" }).q ?? "";
  const navigate = useNavigate();
  const [term, setTerm] = useState(q);
  useEffect(() => setTerm(q), [q]);
  const query = useGlobalSearch(q);
  const data = query.data;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void navigate({ to: "/search", search: term.trim() ? { q: term.trim() } : {} });
  };

  return (
    <section className="ui-page">
      <header className="ui-page-header">
        <h1>{t("search.title")}</h1>
        <p>{t("search.subtitle")}</p>
      </header>
      <form className="ui-filter-bar" role="search" onSubmit={submit}>
        <input type="search" autoComplete="off" value={term} onChange={(event) => setTerm(event.target.value)}
          placeholder={t("search.placeholder")} aria-label={t("search.title")} autoFocus />
        <button type="submit" className="ui-button">{t("search.submit")}</button>
      </form>
      {!q ? (
        <div className="ui-empty">{t("search.empty")}</div>
      ) : query.error ? (
        <div className="ui-alert ui-alert-danger">{(query.error as Error).message}</div>
      ) : !data ? (
        <Loading />
      ) : (
        <>
          <p className="ui-muted">{t("search.summary", { q: data.q, count: data.devices.length + data.customers.length + data.sites.length })}</p>
          <section className="ui-panel ui-admin-section">
            <h2>{t("search.devices")} <span className="ui-chip-count">{data.devices.length}</span></h2>
            {data.devices.length ? (
              <ul className="ui-search-results">
                {data.devices.map((device) => (
                  <li key={device.id}>
                    <Link to="/devices/$deviceId" params={{ deviceId: device.id }} className="ui-search-result">
                      <BrandIcon brand={device.brand} />
                      <span className="ui-search-main">
                        <strong>{device.name}</strong>
                        <small className="ui-muted ui-block">
                          {[device.customer?.name, device.site?.name ?? t("search.noSite"), [device.vendor, device.model].filter(Boolean).join(" ")].filter(Boolean).join(" · ")}
                        </small>
                        <span className="ui-search-ids">
                          {device.management_ip && <code>{t("search.ip")} {device.management_ip}</code>}
                          {device.primary_mac && <code>{t("search.mac")} {device.primary_mac}</code>}
                          {device.serial_number && <code>{t("search.serial")} {device.serial_number}</code>}
                          {device.external_device_id && <code>{t("search.external")} {device.external_device_id}</code>}
                        </span>
                      </span>
                      <span className={`ui-status ui-status-${device.status}`}>{t(`devices.statusLabel.${device.status}`, { defaultValue: device.status })}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="ui-empty">{t("search.noDevices")}</div>
            )}
            {data.devices.length >= 50 && <p className="ui-muted">{t("search.limit")}</p>}
          </section>
          <div className="ui-two-columns">
            <section className="ui-panel ui-admin-section">
              <h2>{t("search.customers")} <span className="ui-chip-count">{data.customers.length}</span></h2>
              {data.customers.length ? (
                <ul className="ui-search-results">
                  {data.customers.map((customer) => (
                    <li key={customer.id}>
                      <Link to="/customers/$customerId" params={{ customerId: customer.id }} className="ui-search-result">
                        <Icon name="customers" />
                        <span className="ui-search-main">
                          <strong>{customer.name}</strong>
                          <small className="ui-muted ui-block">{customer.code ?? t("search.noCode")}</small>
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="ui-empty">{t("search.noCustomers")}</div>
              )}
            </section>
            <section className="ui-panel ui-admin-section">
              <h2>{t("search.sites")} <span className="ui-chip-count">{data.sites.length}</span></h2>
              {data.sites.length ? (
                <ul className="ui-search-results">
                  {data.sites.map((site) => (
                    <li key={site.id}>
                      {site.customer ? (
                        <Link to="/customers/$customerId/sites" params={{ customerId: site.customer.id }} className="ui-search-result">
                          <Icon name="customers" />
                          <span className="ui-search-main">
                            <strong>{site.name}</strong>
                            <small className="ui-muted ui-block">{site.customer.name} · {site.address ?? t("search.noAddress")}</small>
                          </span>
                        </Link>
                      ) : (
                        <span className="ui-search-result">
                          <strong>{site.name}</strong>
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="ui-empty">{t("search.noSites")}</div>
              )}
            </section>
          </div>
        </>
      )}
    </section>
  );
}
