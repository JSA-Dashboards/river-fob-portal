# IT request — one DNS record for a client-facing data service

**What I need:** one **A record**, `api.jpsi.com` pointing to `162.243.174.38`. Nothing else
is needed from IT: no firewall change, no certificate and no mail or Entra change.

**Why:** we have started a small pilot that gives a few clients read-only access to our River
FOB data (barge freight and FOB values) as a web service. It runs on its own small cloud
server and currently has to be reached by raw IP address. A `jpsi.com` name looks professional
to clients, and it lets us move the server later without every client changing their setup.

## The record

| Field | Value |
|---|---|
| Type | **A** |
| Name | **`api.jpsi.com`** |
| Value | **`162.243.174.38`** |
| TTL | 300 seconds (5 minutes), so a correction takes effect quickly. 1 hour is fine once it settles. |
| Proxy | **DNS only.** If jpsi.com's DNS is on a proxying provider such as Cloudflare, please leave the orange-cloud proxy off for this name. The server needs to answer Let's Encrypt validation directly. |

No AAAA (IPv6) record is needed. The server has no IPv6 address.

If `api` is already used for something else, any other name under `jpsi.com` works equally
well (`data.jpsi.com`, `feeds.jpsi.com`). Just tell me which one.

## One thing to check on your side

- **CAA records:** if `jpsi.com` has a **CAA** record, please make sure it allows
  **`letsencrypt.org`** to issue certificates (`0 issue "letsencrypt.org"`). If there is
  no CAA record, nothing to do. The server gets its own free HTTPS certificate from
  Let's Encrypt and renews it automatically.

## About the server (for your records)

- A small DigitalOcean droplet (1 GB, New York region) owned by JSA, created 2026-11-05 for this pilot.
- Only ports 22 (SSH, key-only), 80 and 443 are open. HTTPS is served by Caddy.
- It is read-only: it can only read a small, purpose-made set of **secure views** in Snowflake
  through a read-only service account that is locked to this server's IP. It holds no other JSA
  credentials, no email access and no Entra app.
- Each client gets an individual key, with a 60 calls/minute and 5,000 calls/day limit. Keys
  expire after 6 months and can be revoked at any time.
- It has **no connection to the corporate network**. It sits outside it, and nothing on our network depends on it.

## What happens after the record exists

1. I check it resolves, then point the server's HTTPS certificate at the new name.
2. I update the client guide and the dashboard setting to the new address.
3. The IP address keeps working for a short overlap, so nothing a client already set up breaks.
4. If we ever move the server, you (or I) change one A record and clients are unaffected.

**Contact:** Kolten Postin, kpostin@jpsi.com
