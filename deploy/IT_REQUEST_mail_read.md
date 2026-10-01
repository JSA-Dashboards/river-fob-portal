# IT request — read access to one shared mailbox for an automated job

**What I need:** add **Microsoft Graph `Mail.Read` (Application)** permission to an
**existing** Entra app registration, admin-consent it, and scope it to a single
shared mailbox. This lets a scheduled job read one internal daily email and file
its attachment into a dashboard — replacing a manual copy/paste.

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
   (If a RestrictAccess policy already scopes this app's `Mail.Send` to that
   mailbox, `Mail.Read` is covered by the same policy — nothing more to do.)

3. **Deliver the source email to that mailbox:** the daily "Bid Sheet" email from
   **Doug Schultz (dschultz@jpsi.com)** currently goes to a handful of us. Please
   also deliver it to **`basis-tracker@jpsi.com`** — either add that address to
   Doug's distribution, or a simple mail-flow rule that copies his Bid Sheet email
   there. (Nothing changes for the existing human recipients.)

## Why this is low-risk

- **Application permission + ApplicationAccessPolicy = the app can read exactly one
  shared mailbox** and no one else's mail. It cannot read any user inbox.
- No new app, no new secret, no legacy auth (SMTP/IMAP/app passwords) — it's the
  same modern OAuth app already approved for sending.

Once this is in place the job runs unattended on our server (no one's desktop),
reads that one mailbox daily, and stops touching individual inboxes entirely.
