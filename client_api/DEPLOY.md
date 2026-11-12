# Client API — server setup (runbook)

A separate small DigitalOcean droplet runs only this API. It holds the API's read-only Snowflake key and the code it needs,
nothing else: no repo credentials, no other app's secrets, and no client keys (those live as hashes in Snowflake and are
managed in River FOB's 🔑 API tab). Kolten chose this, "the server's own address for now" and a rolling 12 months on
2026-11-05. The pilot client group is **Hertshten**.

## LIVE since 2026-11-05: https://api.jpsi.com (the name since 2026-11-12; the numeric address was switched off the same day)

**The numeric-address block was removed from the Caddyfile later on 2026-11-12** (backup `/etc/caddy/Caddyfile.bak.20261112b` still has it): `https://162.243.174.38` no longer answers, and the 6-day IP certificate is no longer
renewed. River FOB's `RIVER_FOB_API_URL` and the uptime check were already on the name. To bring the address back, restore that backup (its IP block has the `tls { issuer acme { profile shortlived } }` section).

**2026-11-12: IT added the DNS record `api.jpsi.com` A 162.243.174.38** (DNS only, no proxy). The same day `/etc/caddy/Caddyfile` got a second site block,
`api.jpsi.com { reverse_proxy 127.0.0.1:8000 ... }` with the same two header lines and NO `tls` block (an ordinary Let's Encrypt certificate; Caddy got it over
http-01 in about 5 seconds, issuer YE2, 90 days, renews itself), above the unchanged IP block. Backup: `/etc/caddy/Caddyfile.bak.20261112`. Checked from outside: `GET /v1/health` -> 200 with a trusted
certificate, `/v1/cif` with no key -> 401, and the IP address still 200 at that point. `RIVER_FOB_API_URL` was then set to the name in River FOB's Streamlit secrets (Kolten, same day;
the 🔑 API tab's check showed `https://api.jpsi.com/v1/health -> 200`).

- **Droplet:** `river-fob-api`, NYC1, Ubuntu 24.04.5, 1 GB. Only Kolten's `basis-tracker-droplet` SSH key is on it.
  `ssh root@162.243.174.38`; batch the work into one session, because ufw rate-limits quick reconnects.
- **Packages:** FastAPI 0.162, Starlette 1.10, uvicorn 0.54, snowflake-connector 5.2.0 (`client_api/requirements.lock` on
  the box).
- **Caddy 2.11.9:** Let's Encrypt IP certificate (issuer YE2, 6-day, `shortlived` profile; the first one came at once).
  No contact email: one was set by mistake for the first certificate and then removed.
- **Snowflake:** account `JSA-ANALYTICS`, user `RIVER_FOB_API_SVC` (TYPE=SERVICE, key fingerprint
  `SHA256:YR/6EMroZngklPXDr3iaFVikNmkm80XboKJyziYE1iA=`). User-level network policy `RIVER_FOB_API_ONLY` = this IP.
- **Proved from the box as that user:** the 3 views and ACTIVE_KEYS read. Refused: PUBLIC.CIF_HISTORY, FUTURES_HISTORY,
  API_KEYS, API_CALLS, JSA.*, UPDATE / DELETE / CREATE.
- **End to end over HTTPS with a temporary key** ("JSA deploy check", revoked; it stays in the tab as revoked):
  - 140 sheets, and corn FOB on 11-04 equal to the portal's grid.
  - CSV works; 400 / 401 / 404 as designed.
  - The revoke was refused within about a minute.
  - Calls reached API_CALLS.
- **Two go-live fixes:**
  - The buffered calls are now written in the app's shutdown, because atexit ran too late and a restart lost a batch.
  - Only `/v1/` calls go to the tab's log, because scanners hit `/` and `/favicon.ico` within the first hour.
- **Uptime check** (`client_api/monitor/uptime.sh`, 2026-11-05):
  - **Where it runs:** on the OTHER droplet (137.184.195.51), every 5 minutes, from crontab line
    `*/5 * * * * /bin/bash /opt/river-fob-portal/client_api/monitor/uptime.sh` (that droplet's River FOB clone;
    update = `git pull`).
  - **What counts as up:** HTTPS with a trusted certificate, `"data":"ok"`, and the certificate more than 24 h from
    expiry. One retry 30 s later before it counts as down.
  - **Reporting:** it pings healthchecks.io check `river-fob-api-uptime` (`/fail` with the reason when down), with the
    project ping key in `/opt/alerting/alert.conf`. Log: `journalctl -t river-fob-api-uptime`.
  - **The check itself:** auto-create was refused (404: the healthchecks project is at its check limit, as on 10-23), so
    Kolten makes it by hand: slug `river-fob-api-uptime`, Simple schedule, period 5 min, grace 10 min.
- **Deploying a change:** from the PC, in this repo, run
  `tar --exclude=__pycache__ -cf - client_api fob_model.py net_carry_data.py | ssh root@162.243.174.38 'tar -xf - -C /opt/river-fob-api && chown -R root:root /opt/river-fob-api && systemctl restart river-fob-api'`.
  The journal should then show `Shutdown: wrote N buffered call record(s)` and `Data loaded: ...`.

## 0. Kolten: create the droplet

DigitalOcean → Create → Droplets:
- **Region** New York, datacenter NYC1 (the same as the existing droplet).
- **Image** Ubuntu 24.04 (LTS) x64.
- **Size** Basic, Regular, $6/month (1 GB / 1 CPU).
- **Authentication** SSH Key: tick only **basis-tracker-droplet** (Kolten's key, the one Claude uses). Not Ross's or
  anyone else's: this box holds a Snowflake login.
- **Hostname** `river-fob-api`. Monitoring on. Backups off (nothing on it that can't be rebuilt).

Then send the droplet's **public IPv4 address**.

## 1. Base hardening (as root over SSH; batch into one session: ufw rate-limits quick reconnects)

```bash
apt update && apt -y full-upgrade
apt -y install unattended-upgrades python3-venv ufw
dpkg-reconfigure -f noninteractive unattended-upgrades
ufw default deny incoming && ufw limit OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable
sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/; s/^#\?PermitRootLogin .*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
systemctl reload ssh
timedatectl set-timezone America/Chicago
useradd --system --home /opt/river-fob-api --shell /usr/sbin/nologin rfobapi
mkdir -p /opt/river-fob-api /etc/river-fob-api /var/log/river-fob-api
chown root:rfobapi /etc/river-fob-api && chmod 750 /etc/river-fob-api
chown rfobapi:rfobapi /var/log/river-fob-api
```

## 2. The code (copied, not cloned: the server needs no GitHub access)

From the PC, in `river-fob-portal`:

```bash
scp -r client_api fob_model.py net_carry_data.py root@<IP>:/opt/river-fob-api/
```

On the server:

```bash
cd /opt/river-fob-api && python3 -m venv venv && venv/bin/pip install -r client_api/requirements.txt
venv/bin/pip freeze > client_api/requirements.lock
chown -R root:root /opt/river-fob-api   # the service can read the code but not change it
```

Deploying a change later = the same `scp`, then `systemctl restart river-fob-api`.

## 3. Snowflake: the API's own user (key made ON the server)

```bash
openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out /etc/river-fob-api/snowflake_key.p8
chown rfobapi:rfobapi /etc/river-fob-api/snowflake_key.p8 && chmod 600 /etc/river-fob-api/snowflake_key.p8
openssl rsa -in /etc/river-fob-api/snowflake_key.p8 -pubout | grep -v '^-----' | tr -d '\n'   # the PUBLIC key
```

Then run **step 2 of `client_api/sql/setup.sql`** as ACCOUNTADMIN (from the PC; steps 1 and 1b are already in place). It
creates user `RIVER_FOB_API_SVC` with that public key, gives it RIVER_FOB_API_ROLE, and adds a network policy allowing
sign-in from this server's IP only. The private key never leaves the server.

`/etc/river-fob-api/env` (owner root:rfobapi, mode 640):

```
SNOWFLAKE_ACCOUNT=<same account as the other apps>
SNOWFLAKE_USER=RIVER_FOB_API_SVC
SNOWFLAKE_ROLE=RIVER_FOB_API_ROLE
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_PRIVATE_KEY_PATH=/etc/river-fob-api/snowflake_key.p8
API_ACCESS_LOG=/var/log/river-fob-api/access.log
```

**Prove the limits before going live** (as `RIVER_FOB_API_SVC`, from the server):
- `SELECT * FROM RIVER_FOB.PUBLIC.CIF_HISTORY` fails: no access to the archive itself.
- `SELECT * FROM RIVER_FOB.API_ADMIN.API_KEYS` and `SELECT * FROM RIVER_FOB.API_ADMIN.API_CALLS` both fail. It sees active
  key hashes only, through ACTIVE_KEYS, and can only add call rows.
- `SELECT MIN(as_of) FROM RIVER_FOB.CLIENT_API.CIF_12M` is about a year back.
- A sign-in with the key from any other machine fails (the network policy).

## 4. The service

`/etc/systemd/system/river-fob-api.service`:

```ini
[Unit]
Description=River FOB client API
After=network-online.target
Wants=network-online.target

[Service]
User=rfobapi
WorkingDirectory=/opt/river-fob-api
EnvironmentFile=/etc/river-fob-api/env
ExecStart=/opt/river-fob-api/venv/bin/uvicorn client_api.app:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1 --no-server-header --workers 1
Restart=always
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/log/river-fob-api

[Install]
WantedBy=multi-user.target
```

One worker on purpose: the rate limits, the data cache and the call buffer live in that one process. A restart writes the
buffered calls to Snowflake before it exits.

```bash
systemctl daemon-reload && systemctl enable --now river-fob-api
curl -s localhost:8000/v1/health
```

## 5. HTTPS: Caddy in front

Install Caddy (official apt repository, caddyserver.com/docs/install), then `/etc/caddy/Caddyfile`:

```
<IP> {
    tls {
        issuer acme {
            profile shortlived
        }
    }
    reverse_proxy 127.0.0.1:8000
    header -Server
    header Strict-Transport-Security "max-age=31536000"
}
```

- Let's Encrypt has issued IP-address certificates since early 2026: 6-day certificates (the `shortlived` profile), which
  Caddy renews by itself.
- If issuance fails (Caddy's IPv4 IP-certificate support has had rough edges), use the free hostname
  `<IP with dashes>.sslip.io` as the site address. It resolves to the IP and takes an ordinary certificate.
- When IT later adds `api.jpsi.com`, change the site address to that and give the client the new URL.

`/etc/logrotate.d/river-fob-api`: weekly, keep 26, `copytruncate`, for `/var/log/river-fob-api/access.log`.

## 6. River FOB app and the client's key

- In River FOB's Streamlit Cloud **Secrets** add `RIVER_FOB_API_URL = "https://<the address from step 5>"`, then Reboot.
  The 🔑 API tab then shows the address and a "Check the API server" button.
- In the 🔑 API tab: **Create a key**, Client group `Hertshten`, a note saying how it'll be sent. Copy the key from the
  box (it's shown once), then "I've stored it".
- Send the key through a password-manager share or a one-time secret link, never plain email or chat. Send
  `client_api/README.md` with it, the base URL filled in.
- Revoke from the same tab. It reaches the server within about a minute.

## 7. Check from outside

- `/v1/health` → 200.
- Any data path without the key → 401.
- With the key: `/v1/dates`, then `/v1/fob?commodity=corn&location=Hennepin` matches the portal's Hennepin corn row for
  that date.
- `/docs` → 404.
- `ssh` with a password → refused.
- Within ~5 minutes the calls appear in the 🔑 API tab's Calls list, each with its key id and never the key.
