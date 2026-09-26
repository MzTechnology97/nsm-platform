# Network Security Platform Core 0.1

Core iniziale con FastAPI/Uvicorn, PostgreSQL 18, Redis, worker, Caddy, Alembic, login Argon2, CSRF, Clienti, Sedi, Apparati e audit events.

## Installazione

Dalla directory estratta:

```bash
chmod +x install_core.sh manage.sh
./install_core.sh
```

Poi:

```bash
cd /srv/network-platform
./manage.sh create-admin
```

Apri `http://IP-DEL-SERVER/`.

Per ora il portale usa HTTP in LAN e `SESSION_COOKIE_SECURE=false`. Prima di esporlo pubblicamente configureremo FQDN + HTTPS e abiliteremo il cookie Secure.

Comandi utili:

```bash
./manage.sh status
./manage.sh logs
./manage.sh logs api
./manage.sh health
./manage.sh db-shell
```

Non condividere `/srv/network-platform/secrets/bootstrap.env`.
