# IT request — read access to one shared mailbox for an automated job

**What I need:** add **Microsoft Graph `Mail.Read` (Application)** permission to an
**existing** Entra app registration, admin-consent it, and scope it to a single
shared mailbox. This lets a scheduled job read one internal daily email and file
its attachment into a dashboard. Today a job on my laptop does it through Outlook,
and it misses the day whenever the laptop is asleep. On Wed 11/04 the 2:00 PM sheet
wasn't filed until 3:39 AM the next day.

This reuses the app you already set up for us; it's a small, least-privilege add.

## The existing app (already in our tenant)

- **App (registration) name:** `Basis Tracker`
- **Application (client) ID:** `19283e00-a7fa-4b0c-910c-5b9a85b8e990`
- **Tenant:** jpsi.com (`4a4f2e28-2f12-4cdb-b5eb-9860e3af1045`)
- It already has **`Mail.Send` (Application)**, admin-consented, used to send as
  **`basis-tracker@jpsi.com`**. I'm asking to add **read of that same mailbox**.

## The three steps

1. **Add the API permission:** on app `19283e00…`, add Microsoft Graph →
   Application permission → **`Mail.Read`**, then **Grant admin consent**.

2. **Scope it to one mailbox (least privilege):** create/extend an
   **ApplicationAccessPolicy** so this app can read **only** `basis-tracker@jpsi.com`,
   not the whole tenant. In Exchange Online PowerShell:

   ```powershell
   New-ApplicationAccessPolicy `
     -AppId 19283e00-a7fa-4b0c-910c-5b9a85b8e990 `
     -PolicyScopeGroupId basis-tracker@jpsi.com `
     -AccessRight RestrictAccess `
     -Description "River FOB bid-sheet reader - read only the basis-tracker shared mailbox"
   ```
   - If a RestrictAccess policy already scopes this app's `Mail.Send` to that mailbox,
     `Mail.Read` is covered by the same policy. Nothing more to do.
   - The policy also limits sending to `basis-tracker@jpsi.com`. That's the only address
     the app sends as today, so nothing we run changes.
   - Prefer Exchange Online **RBAC for Applications**? That works too: assign the
     "Application Mail.Read" role with a management scope covering only
     `basis-tracker@jpsi.com`. In that case, skip the Entra `Mail.Read` grant in step 1.
     That grant applies to every mailbox and would be added on top of the scoped role.
   - Would rather keep the sending app untouched? A **separate read-only app** with the
     same scoped read works as well. Then I'd need its client ID and secret value. Please
     send the secret through a password-manager share or a one-time link, not plain email.

3. **Deliver the source email to that mailbox:** the daily "Bid Sheet" email from
   **Doug Schultz (dschultz@jpsi.com)** currently goes to a handful of us. Please
   also deliver it to **`basis-tracker@jpsi.com`** — either add that address to
   Doug's distribution, or a simple mail-flow rule that copies his Bid Sheet email
   there. (Nothing changes for the existing human recipients.)

## Why this is low-risk

- **Application permission + ApplicationAccessPolicy = the app can read exactly one
  shared mailbox** and no one else's mail. It cannot read any user inbox.
- It only reads: it never sends, moves or deletes anything in that mailbox.
- No legacy auth (SMTP/IMAP/app passwords) — it's the same modern OAuth app already
  approved for sending.

Once this is in place the job runs unattended on our server (no one's desktop),
reads that one mailbox daily, and stops touching individual inboxes entirely.

---

## For us, once IT is done (not part of the request)

1. **Same app (steps as written):** nothing new goes in the `.env`. `fetch_bidsheet_graph.py` uses
   `GRAPH_CLIENT_ID` / `GRAPH_CLIENT_SECRET`, already in `/opt/river-fob-portal/.env`.
   **Separate app:** add `GRAPH_READ_CLIENT_ID` + `GRAPH_READ_CLIENT_SECRET` there. Leave the `GRAPH_CLIENT_*`
   pair alone, because the 5 PM freshness alert sends with it.
   Either way, set `GRAPH_BIDSHEET_MAILBOX=basis-tracker@jpsi.com`.
2. `python fetch_bidsheet_graph.py --check`: the token's roles should now include `Mail.Read` (unless IT used RBAC
   for Applications, which doesn't show in the token), and it should find Doug's newest sheet. It saves nothing.
3. Check the limit holds: reading any OTHER mailbox with the app must fail with 403.
4. Add the droplet cron job. The desktop task can then stay on as a backup, since the two writers can't
   duplicate a day (CLAUDE.md "Two writers, no duplicates").
