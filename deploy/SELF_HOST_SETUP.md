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

## Phase 2 — expose it over TLS at a real hostname

Pick a hostname (see the note in `nginx-river-fob.conf`). Everything below uses
`RIVERFOB_HOST` as a stand-in.

### 5. DNS (Cloudflare)

Add an **A record**: `RIVERFOB_HOST` → `137.184.195.51`, **Proxied** (orange
cloud) so Cloudflare terminates the client TLS. Confirm the Cloudflare SSL mode
for the zone is **Full (strict)** (it already is if the WhatsApp host works).

### 6. nginx vhost

```bash
sudo cp /opt/river-fob-portal/deploy/nginx-river-fob.conf /etc/nginx/sites-available/river-fob
sudo nano /etc/nginx/sites-available/river-fob     # set server_name = RIVERFOB_HOST;
                                                   # set the two ssl_certificate lines
                                                   # (copy them from the wa.jsa-whatsapp.us vhost)
sudo ln -s /etc/nginx/sites-available/river-fob /etc/nginx/sites-enabled/river-fob
sudo nginx -t && sudo systemctl reload nginx
```

### 7. Verify

Browse **https://RIVERFOB_HOST/?view=1** — it should load fast and switch tabs
instantly, with a valid padlock. Check the socket connected (no "Connecting…"
spinner stuck).

### 8. Cut over

Hand clients the new URL. **Keep the Streamlit Cloud app up as a fallback** for a
week or two before retiring it. Once you retire Cloud, remove the `warm_ping.sh`
cron line (it only existed to keep Cloud awake).

---

## Operating it

- **Update code:** `cd /opt/river-fob-portal && git pull && sudo systemctl restart river-fob`
  (add `.venv/bin/pip install -r requirements.txt` only if deps changed). No
  Streamlit Cloud reboot dance — a restart is instant.
- **Logs:** `journalctl -u river-fob -f`
- **The FOB Vessel cron is unaffected** — it still writes to Snowflake on its own
  schedule; the app just reads it.
- **Failure alerting:** consider adding a healthchecks.io check that pings
  `https://RIVERFOB_HOST/_stcore/health`, so you're told if the app goes down
  (the systemd `Restart=always` handles crashes, but not a wedged process).

### Notes / hardening (optional)
- Runs as `root` to match the box's convention. A dedicated `riverfob` user with
  ownership of `/opt/river-fob-portal` is tidier if you want to harden later.
- The 1 vCPU / 2GB box also runs the WhatsApp bridge + cron jobs. The Streamlit
  app is light at idle; if memory gets tight, that's the first thing to watch
  (`systemctl status river-fob`, `free -m`).
