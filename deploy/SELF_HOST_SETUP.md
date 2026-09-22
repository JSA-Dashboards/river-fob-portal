# River FOB Portal — self-host on the DigitalOcean Droplet

Runs the Streamlit app itself on the Droplet (137.184.195.51) instead of
Streamlit Community Cloud, to escape the shared-container throttling and the
sleep/cold-start. The app sits next to the warm Snowflake path and never sleeps.
nginx (already on the box for the WhatsApp bridge) reverse-proxies it over TLS.

The Droplet already has `/opt/river-fob-portal` cloned (for the FOB Vessel cron)
with a `.venv` and a `.env`. This reuses both.

**Do Phase 1 first and confirm it's actually fast before touching DNS/TLS.**

---

## Phase 1 — run it on localhost (no DNS, no TLS, zero client impact)

### 1. Upgrade the venv to the full app dependencies

The existing `.venv` only has the job deps (snowflake/requests/dotenv). The app
needs the whole stack:

```bash
cd /opt/river-fob-portal && git pull
.venv/bin/pip install -r requirements.txt      # superset of requirements-jobs.txt
```

### 2. Make sure the `.env` has the app-only secrets

The FOB Vessel job's `.env` already has `USE_SNOWFLAKE`, the `SNOWFLAKE_*` block,
and `FOB_VESSEL_*`. The **app** additionally needs these — add any that are
missing (the app reads them from `.env` via `load_dotenv()`):

```bash
grep -E '^(MASSIVE_API_KEY|EDIT_PASSWORD)=' /opt/river-fob-portal/.env || echo "MISSING — add them"
# if missing, append (values from the Streamlit Cloud secrets):
#   echo 'MASSIVE_API_KEY=...' >> /opt/river-fob-portal/.env
#   echo 'EDIT_PASSWORD=jpsi'  >> /opt/river-fob-portal/.env
```

`MASSIVE_API_KEY` powers the live fed-funds interest rate; `EDIT_PASSWORD` gates
the editable build (clients use `?view=1`, which needs no password).

### 3. Install and start the service

```bash
sudo cp /opt/river-fob-portal/deploy/river-fob.service /etc/systemd/system/river-fob.service
sudo systemctl daemon-reload
sudo systemctl enable --now river-fob
sudo systemctl status river-fob --no-pager        # should be active (running)
```

Watch it come up (Snowflake connect + first render):

```bash
journalctl -u river-fob -f          # Ctrl-C once you see "You can now view your Streamlit app"
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8525/_stcore/health   # -> 200
```

### 4. Test the speed over an SSH tunnel (from your PC, PowerShell)

```bash
ssh -L 8525:localhost:8525 root@137.184.195.51
```

Leave that open, then browse **http://localhost:8525/?view=1** in your own
browser. This is the real app, served by the Droplet, no Cloud involved. Click
around the tabs and judge the speed. If it's clearly better, go to Phase 2. If
not, stop here — `sudo systemctl disable --now river-fob` and we rethink.

---

## Phase 2 — expose it over TLS at riverfob.jpsi.com

Hostname: **riverfob.jpsi.com** (change the label if you prefer, e.g.
`fob.jpsi.com` — keep it identical across the DNS record, the cert, and the
nginx `server_name`). jpsi.com is not on the box's Cloudflare zone, so we use a
free **Let's Encrypt** cert via certbot.

### 5. DNS (jpsi.com — may need IT)

Add an **A record**: `riverfob.jpsi.com` → `137.184.195.51`, **DNS-only** (not
proxied — certbot's HTTP-01 challenge and nginx serve this host directly). If
jpsi.com's DNS is managed by IT, this is the one thing to hand off. Confirm it
resolves before continuing:

```bash
dig +short riverfob.jpsi.com          # should print 137.184.195.51
```

### 6. nginx vhost + cert

```bash
sudo cp /opt/river-fob-portal/deploy/nginx-river-fob.conf /etc/nginx/sites-available/river-fob
# (edit server_name if you chose a different label; see the map-collision note
#  in the file if the wa.jsa-whatsapp.us vhost already defines $connection_upgrade)
sudo ln -s /etc/nginx/sites-available/river-fob /etc/nginx/sites-enabled/river-fob
sudo nginx -t && sudo systemctl reload nginx

# Obtain the cert and let certbot add the TLS block + HTTP->HTTPS redirect:
sudo apt install -y certbot python3-certbot-nginx        # if not already present
sudo certbot --nginx -d riverfob.jpsi.com
```

certbot installs a renewal timer automatically; nothing else to do for renewals.

### 7. Verify

Browse **https://riverfob.jpsi.com/?view=1** — valid padlock, loads fast, tabs
switch instantly, no stuck "Connecting…" spinner. Then check the edit build:
**https://riverfob.jpsi.com/** should prompt for `EDIT_PASSWORD`.

### 8. Cut over

Hand clients `https://riverfob.jpsi.com/?view=1`. **Keep the Streamlit Cloud app
up as a fallback** for a week or two before retiring it. Once you retire Cloud,
remove the `warm_ping.sh` cron line (it only existed to keep Cloud awake).

---

## Operating it

- **Update code:** `cd /opt/river-fob-portal && git pull && sudo systemctl restart river-fob`
  (add `.venv/bin/pip install -r requirements.txt` only if deps changed). No
  Streamlit Cloud reboot dance — a restart is instant.
- **Logs:** `journalctl -u river-fob -f`
- **The FOB Vessel cron is unaffected** — it still writes to Snowflake on its own
  schedule; the app just reads it.
- **Failure alerting:** consider adding a healthchecks.io check that pings
  `https://riverfob.jpsi.com/_stcore/health`, so you're told if the app goes down
  (the systemd `Restart=always` handles crashes, but not a wedged process).

### Notes / hardening (optional)
- Runs as `root` to match the box's convention. A dedicated `riverfob` user with
  ownership of `/opt/river-fob-portal` is tidier if you want to harden later.
- The 1 vCPU / 2GB box also runs the WhatsApp bridge + cron jobs. The Streamlit
  app is light at idle; if memory gets tight, that's the first thing to watch
  (`systemctl status river-fob`, `free -m`).
