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
