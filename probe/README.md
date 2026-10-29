# NSM external probe

The NSM server usually sits inside the operator network, so a check of a customer's public address from there never crosses the border filters. The probe runs the same port checks from a host **outside** the network (any VPS with another Internet connection) and answers only signed requests from NSM.

## Install

On the outside host, with Docker:

```bash
git clone <this repository> nsm && cd nsm/probe
cp .env.example .env
# PROBE_SECRET: openssl rand -hex 32  (the same value goes in NSM)
# PROBE_ALLOWED_SOURCES: the public IP the NSM server goes out from
docker compose up -d --build
```

Then in NSM: *Amministrazione → Integrazioni → Sonda esterna*, enter the address (`http://<host>:8788`, or `https://` behind a reverse proxy), the name and the secret, save and press *Prova la sonda*.

## What it does

- `GET /v1/health`: name and version.
- `POST /v1/scan`: the checks NSM plans for each public address of its inventory (TCP connect, one harmless datagram for DNS, SNMP `public`, SSDP and Ubiquiti discovery). The request is signed with HMAC-SHA256 of `"<timestamp>.<body>"`, and requests older than 5 minutes are refused.
- Limits:
  - public unicast addresses only;
  - 32 addresses and 64 checks per request;
  - `PROBE_MAX_REQUESTS` requests per minute;
  - callers limited to `PROBE_ALLOWED_SOURCES`.
- Nothing is stored and nothing runs on its own. The container is read-only, without capabilities, as an unprivileged user.

The checks are the same code NSM uses (`app/app/port_probes.py`, copied into the image).
