-- River FOB client API: the read-only door into Snowflake. Run as ACCOUNTADMIN. Safe to re-run.
--
-- The API's user can read ONLY three secure views in RIVER_FOB.CLIENT_API, which already cut the archive to the last
-- ~12 months (366 days; the API itself enforces exactly 365 in Central time) and to corn / soybeans / wheat and the 7
-- live freight regions. No futures, no other table, no other schema or database. Secure views run with their owner's
-- rights, so the role needs no grant on RIVER_FOB.PUBLIC at all, and their definitions are hidden from it.

USE ROLE ACCOUNTADMIN;

CREATE SCHEMA IF NOT EXISTS RIVER_FOB.CLIENT_API
  COMMENT = 'Client API (pilot 2026-11): secure views over the River FOB archive, last 12 months only';

CREATE OR REPLACE SECURE VIEW RIVER_FOB.CLIENT_API.CIF_12M
  COMMENT = 'CIF NOLA ($/bu basis) by sheet date / commodity / month label, last 366 days'
AS SELECT as_of, commodity, month, value
   FROM RIVER_FOB.PUBLIC.CIF_HISTORY
   WHERE as_of >= TO_VARCHAR(DATEADD(day, -366, CURRENT_DATE()), 'YYYY-MM-DD')
     AND commodity IN ('Corn', 'Soybeans', 'Wheat');

CREATE OR REPLACE SECURE VIEW RIVER_FOB.CLIENT_API.FREIGHT_12M
  COMMENT = 'Barge freight (multiple of tariff: 7.25 = 725%) by sheet date / region / month label, last 366 days'
AS SELECT as_of, region, month, value
   FROM RIVER_FOB.PUBLIC.FREIGHT_HISTORY
   WHERE as_of >= TO_VARCHAR(DATEADD(day, -366, CURRENT_DATE()), 'YYYY-MM-DD')
     AND region IN ('IL', 'STL', 'Upper Miss', 'Davenport South', 'McGregor South', 'Ohio', 'Lower Miss');

CREATE OR REPLACE SECURE VIEW RIVER_FOB.CLIENT_API.CALENDAR_12M
  COMMENT = 'Each sheet''s month columns and the futures contract code each is quoted off (no prices), last 366 days'
AS SELECT as_of, commodity, seq, month, contract
   FROM RIVER_FOB.PUBLIC.CALENDAR_HISTORY
   WHERE as_of >= TO_VARCHAR(DATEADD(day, -366, CURRENT_DATE()), 'YYYY-MM-DD')
     AND commodity IN ('Corn', 'Soybeans', 'Wheat');

CREATE ROLE IF NOT EXISTS RIVER_FOB_API_ROLE
  COMMENT = 'River FOB client API: SELECT on RIVER_FOB.CLIENT_API views only';
GRANT USAGE ON WAREHOUSE COMPUTE_WH TO ROLE RIVER_FOB_API_ROLE;
GRANT USAGE ON DATABASE RIVER_FOB TO ROLE RIVER_FOB_API_ROLE;
GRANT USAGE ON SCHEMA RIVER_FOB.CLIENT_API TO ROLE RIVER_FOB_API_ROLE;
GRANT SELECT ON VIEW RIVER_FOB.CLIENT_API.CIF_12M TO ROLE RIVER_FOB_API_ROLE;
GRANT SELECT ON VIEW RIVER_FOB.CLIENT_API.FREIGHT_12M TO ROLE RIVER_FOB_API_ROLE;
GRANT SELECT ON VIEW RIVER_FOB.CLIENT_API.CALENDAR_12M TO ROLE RIVER_FOB_API_ROLE;

-- ── Step 1b: keys and the call log (2026-11-05: managed from River FOB's 🔑 API tab) ─────────────────────────────────
-- The tab (River FOB's Cloud app, RIVER_FOB_ROLE; the admin portal inherits that role but doesn't show the tab) creates and
-- revokes keys and reads the calls. The API server (RIVER_FOB_API_ROLE) sees only ACTIVE key hashes, through a secure
-- view, and can only ADD call rows: it can't read the log, change a key or see a revoked one. Nobody stores a raw key.

CREATE SCHEMA IF NOT EXISTS RIVER_FOB.API_ADMIN
  COMMENT = 'Client API keys (hashes only) and the call log, managed from the River FOB app''s API tab';

CREATE TABLE IF NOT EXISTS RIVER_FOB.API_ADMIN.API_KEYS (
  key_id      STRING NOT NULL PRIMARY KEY,     -- k_ + 8 hex, shown in the tab and the log
  client      STRING NOT NULL,                 -- the client group, e.g. Hertshten
  key_hash    STRING NOT NULL,                 -- SHA-256 hex of the key (the key itself is shown once, never stored)
  key_hint    STRING,                          -- its first 9 characters ('rfob_Ab12'), to tell keys apart
  note        STRING,
  created_at  TIMESTAMP_NTZ NOT NULL,          -- UTC
  created_by  STRING,
  active      BOOLEAN NOT NULL DEFAULT TRUE,
  revoked_at  TIMESTAMP_NTZ,
  revoked_by  STRING,
  expires_at  TIMESTAMP_NTZ                    -- UTC, 6 months after it was made (or extended)
);

-- Keys expire after 6 months (Kolten 2026-11-05: "make the apis good for 6 months"). For a table made before then: add the
-- column and give every key without one an expiry 6 months after it was made. The SERVER enforces the expiry: the view
-- below only hands it over, because a view that compared with the current time couldn't use Snowflake's result cache.
ALTER TABLE RIVER_FOB.API_ADMIN.API_KEYS ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP_NTZ;
UPDATE RIVER_FOB.API_ADMIN.API_KEYS SET expires_at = DATEADD(month, 6, created_at) WHERE expires_at IS NULL;

CREATE TABLE IF NOT EXISTS RIVER_FOB.API_ADMIN.API_CALLS (
  ts      TIMESTAMP_NTZ NOT NULL,              -- UTC
  key_id  STRING,                              -- NULL for a call with no / a wrong key
  client  STRING,
  path    STRING,
  query   STRING,                              -- our own parameters as sent, any other value as '***'
  status  NUMBER,
  rows_n  NUMBER,
  ms      NUMBER,
  ip      STRING
);

CREATE OR REPLACE SECURE VIEW RIVER_FOB.API_ADMIN.ACTIVE_KEYS COPY GRANTS
  COMMENT = 'What the API server may read: active keys, hash, client and expiry only'
AS SELECT key_id, client, key_hash, expires_at FROM RIVER_FOB.API_ADMIN.API_KEYS WHERE active;

GRANT USAGE ON SCHEMA RIVER_FOB.API_ADMIN TO ROLE RIVER_FOB_API_ROLE;
GRANT SELECT ON VIEW RIVER_FOB.API_ADMIN.ACTIVE_KEYS TO ROLE RIVER_FOB_API_ROLE;
GRANT INSERT ON TABLE RIVER_FOB.API_ADMIN.API_CALLS TO ROLE RIVER_FOB_API_ROLE;

GRANT USAGE ON SCHEMA RIVER_FOB.API_ADMIN TO ROLE RIVER_FOB_ROLE;
GRANT SELECT, INSERT, UPDATE ON TABLE RIVER_FOB.API_ADMIN.API_KEYS TO ROLE RIVER_FOB_ROLE;
GRANT SELECT ON TABLE RIVER_FOB.API_ADMIN.API_CALLS TO ROLE RIVER_FOB_ROLE;

-- ── Step 2, once the API server exists ───────────────────────────────────────────────────────────────────────────────
-- The key pair is made ON the server (client_api/DEPLOY.md); only its PUBLIC key comes here. The network policy lets
-- this user sign in from the server's address only, so a copied key is useless anywhere else.
--
-- CREATE USER IF NOT EXISTS RIVER_FOB_API_SVC
--   TYPE = SERVICE DEFAULT_ROLE = RIVER_FOB_API_ROLE DEFAULT_WAREHOUSE = COMPUTE_WH
--   RSA_PUBLIC_KEY = '<public key, base64, no header lines>'
--   COMMENT = 'River FOB client API server';
-- GRANT ROLE RIVER_FOB_API_ROLE TO USER RIVER_FOB_API_SVC;
-- CREATE NETWORK POLICY IF NOT EXISTS RIVER_FOB_API_ONLY ALLOWED_IP_LIST = ('<server IP>');
-- ALTER USER RIVER_FOB_API_SVC SET NETWORK_POLICY = RIVER_FOB_API_ONLY;
