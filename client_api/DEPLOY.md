# Client API — server setup (runbook)

A separate small DigitalOcean droplet runs only this API. It holds the API's read-only Snowflake key, the hashed client
keys and the code it needs, and nothing else (no repo credentials, no other app's secrets). Kolten chose this,
"the server's own address for now" and a rolling 12 months on 2026-11-05.

## 0. Kolten: create the droplet

DigitalOcean → Create → Droplet:
- **Image** Ubuntu 24.04 LTS x64.
- **Size** Basic, Regular, 1 vCPU / 1 GB (about $6/month).
- **Region** the same as the existing droplet.
- **Authentication** SSH key: the same key as the existing droplet (so Claude can set it up). No password.
- **Hostname** `river-fob-api`. Monitoring on. Backups not needed (nothing on it that can't be rebuilt, except
  the keys file; re-issuing a key is one command).

Then send the droplet's **IPv4 address**.

## 1. Base hardening (as root over SSH)

```bash
apt update && apt -y full-upgrade
apt -y install unattended-upgrades python3-venv ufw
dpkg-reconfigure -f noninteractive unattended-upgrades
ufw default deny incoming && ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable
sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/; s/^#\?PermitRootLogin .*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
systemctl reload ssh
useradd --system --home /opt/river-fob-api --shell /usr/sbin/nologin rfobapi
mkdir -p /opt/river-fob-api /etc/river-fob-api /var/log/river-fob-api
chown rfobapi:rfobapi /etc/river-fob-api /var/log/river-fob-api && chmod 750 /etc/river-fob-api
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
openssl rsa -in /etc/river-fob-api/snowflake_key.p8 -pubout | grep -v '^-----'   # the PUBLIC key, for step 2 of setup.sql
```

Then run **step 2 of `client_api/sql/setup.sql`** as ACCOUNTADMIN (from the PC). It creates user `RIVER_FOB_API_SVC`
with that public key, gives it RIVER_FOB_API_ROLE, and adds a network policy allowing sign-in from this server's IP
only. The private key never leaves the server.

`/etc/river-fob-api/env` (owner rfobapi, mode 600):

```
SNOWFLAKE_ACCOUNT=<same account as the other apps>
SNOWFLAKE_USER=RIVER_FOB_API_SVC
SNOWFLAKE_ROLE=RIVER_FOB_API_ROLE
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_PRIVATE_KEY_PATH=/etc/river-fob-api/snowflake_key.p8
API_KEYS_FILE=/etc/river-fob-api/keys.json
API_ACCESS_LOG=/var/log/river-fob-api/access.log
```

**Prove the limits before going live:**
- As `RIVER_FOB_API_SVC`, `SELECT * FROM RIVER_FOB.PUBLIC.CIF_HISTORY` must fail.
- A sign-in with the key from any other machine must fail (the network policy).
- `SELECT MIN(as_of) FROM RIVER_FOB.CLIENT_API.CIF_12M` must be about a year back.

## 4. The service

`/etc/systemd/system/river-fob-api.service`:

```ini
[Unit]
Description=River FOB client API
After=network-online.target

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
ReadWritePaths=/etc/river-fob-api /var/log/river-fob-api

[Install]
WantedBy=multi-user.target
```

One worker on purpose: the rate limits and the cache live in that one process.

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

Let's Encrypt has issued IP-address certificates since early 2026. They're 6-day certificates (the `shortlived` profile)
and Caddy renews them by itself. If issuance fails (Caddy's IPv4 IP-certificate support has had rough edges), use the
free hostname `<IP with dashes>.sslip.io`, which resolves to the IP, as the site address with an ordinary certificate.
When IT later adds `api.jpsi.com`, change the site address to that and give the client the new URL.

`/etc/logrotate.d/river-fob-api`: weekly, keep 26, `copytruncate`, for `/var/log/river-fob-api/access.log`.

## 6. The client's key

```bash
cd /opt/river-fob-api && sudo -u rfobapi env API_KEYS_FILE=/etc/river-fob-api/keys.json venv/bin/python -m client_api.manage_keys create "<client group>" --note "pilot; sent via <how>"
```

- **Delivery:** send the key through a password-manager share or a one-time secret link, never plain email or chat.
- **With it:** client_api/README.md, the base URL filled in.
- **Revoke:** `... manage_keys revoke <id>`. Takes effect on the client's next call.

## 7. Check from outside

- `/v1/health` → 200.
- Any data path without the key → 401.
- With the key: `/v1/dates`, then `/v1/fob?commodity=corn&location=Hennepin` matches the portal's Hennepin corn row
  for that date.
- `/docs` → 404.
- `ssh` with a password → refused.
- The access log shows each call with the key id, never the key.
