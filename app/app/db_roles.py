"""Least-privilege database roles (run by the ``migrate`` service after alembic).

``nsm_syslog`` — used by the internet-facing syslog receiver:

- SELECT on ``devices`` and ``connector_integrations`` (identification, settings);
- INSERT/SELECT on ``device_log_entries`` and ``device_auth_events``;
- INSERT/SELECT/UPDATE on ``syslog_unknown_sources`` (counters only).

The role is created or its password updated from ``SYSLOG_DB_PASSWORD``; when
the variable is missing nothing is done and the receiver keeps the main account.
Idempotent: safe to run on every deploy.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import text

ROLE = "nsm_syslog"
GRANTS = (
    ("SELECT", ("devices", "connector_integrations")),
    ("SELECT, INSERT", ("device_log_entries", "device_auth_events")),
    ("SELECT, INSERT, UPDATE", ("syslog_unknown_sources",)),
)
SEQUENCES = ("device_log_entries_id_seq", "device_auth_events_id_seq")
log = logging.getLogger("nsm.db_roles")


def ensure_syslog_role(engine, password: str | None) -> bool:
    if not password:
        log.info("SYSLOG_DB_PASSWORD not set: dedicated syslog role skipped")
        return False
    from psycopg import sql

    with engine.connect() as connection:
        raw = connection.connection.driver_connection
        with raw.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE,))
            verb = "ALTER" if cursor.fetchone() else "CREATE"
            cursor.execute(sql.SQL(verb + " ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD {}").format(
                sql.Identifier(ROLE), sql.Literal(password)))
            cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(raw.info.dbname), sql.Identifier(ROLE)))
            cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(ROLE)))
            cursor.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(sql.Identifier(ROLE)))
            for privileges, tables in GRANTS:
                for table in tables:
                    cursor.execute(sql.SQL("GRANT " + privileges + " ON TABLE {} TO {}").format(sql.Identifier(table), sql.Identifier(ROLE)))
            for sequence in SEQUENCES:
                cursor.execute(sql.SQL("GRANT USAGE, SELECT ON SEQUENCE {} TO {}").format(sql.Identifier(sequence), sql.Identifier(ROLE)))
        raw.commit()
    return True


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    from app.db import engine

    if ensure_syslog_role(engine, os.getenv("SYSLOG_DB_PASSWORD")):
        log.info("Dedicated role %s ready", ROLE)


if __name__ == "__main__":
    main()
