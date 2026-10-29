# NSM external probe

The NSM server usually sits inside the operator network, so a check of a customer's public address from there never crosses the border filters. The probe runs the same port checks from a host **outside** the network (any VPS with another Internet connection) and answers only signed requests from NSM.

## Install

On the outside host (Debian or Ubuntu, as root), with the installer:

```bash
curl -fsSL https://raw.githubusercontent.com/MzTechnology97/nsm-platform/main/probe/install.sh -o install.sh
sudo bash install.sh --allow <public IP of the NSM server> --name probe-1
```

The installer:

- installs Docker Engine and the compose plugin from download.docker.com when they are missing;
- downloads `probe/nsm_probe.py` and `app/app/port_probes.py` from this repository (`--ref` picks a tag or branch, `--source` uses a local checkout), so no clone is needed;
- writes `/opt/nsm-probe` (`build/`, `docker-compose.yml`, `.env` with mode 600, `probe.conf` with the choices);
- generates the shared secret (`--secret` sets one), builds the image and starts it with a healthcheck and rotated logs;
- prints the address, name and secret to enter in NSM.

With `--domain probe.example.net` (a name pointing to the host, ports 80 and 443 reachable) the probe answers on `https://probe.example.net` through Caddy with a Let's Encrypt certificate (`--email` for the ACME account). Port 8788 is then not published, and Caddy accepts only the `--allow` addresses (the probe would only see Caddy's address).

Other commands: `status`, `update` (downloads the probe again and rebuilds), `show-secret`, `uninstall`. Rerunning `install` keeps the secret and the saved choices, overriding only the options given.

Then in NSM: *Amministrazione → Integrazioni → Sonda esterna*, enter the address (`http://<host>:8788`, or `https://<domain>`), the name and the secret, save and press *Prova la sonda*.

Docker publishes the port past `ufw`: the source restriction is `--allow` (enforced by the probe, or by Caddy with `--domain`), not the host firewall.

### Manual install

From a checkout of this repository, without the installer:

```bash
cd probe
cp .env.example .env
# PROBE_SECRET: openssl rand -hex 32  (the same value goes in NSM)
# PROBE_ALLOWED_SOURCES: the public IP the NSM server goes out from
docker compose up -d --build
```

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
