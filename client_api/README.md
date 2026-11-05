# JSA River FOB API — client guide (pilot)

CIF NOLA, barge freight and FOB barge values from John Stewart & Associates' daily River FOB sheet, for corn,
soybeans and wheat, over the **last 12 months** of published sheets.

- **Base URL:** `https://162.243.174.38` (all paths below start with `/v1/`). It will move to a JSA web address later;
  we'll tell you before it does.
- **Your key:** send it in the `X-API-Key` header on every call. Keep it private: it identifies your organisation,
  and every call made with it is logged. Never put it in a URL. If it may have leaked, tell us and we'll replace it.
- **Formats:** JSON (default) or CSV (`format=csv`, opens directly in Excel).
- **Updates:** one sheet per business day, normally saved mid-to-late afternoon Central time. Data is at most
  15 minutes behind the saved sheet.

## Endpoints

| Path | Parameters | Returns |
|---|---|---|
| `/v1/dates` | — | the sheet dates available to you, newest first |
| `/v1/locations` | — | the 18 FOB locations (river, freight region), the 7 freight regions, the commodities |
| `/v1/cif` | `commodity`, `start`, `end`, `format` | CIF NOLA basis by delivery month |
| `/v1/freight` | `region`, `start`, `end`, `format` | barge freight by river region and delivery month |
| `/v1/fob` | `commodity`, `location`, `start`, `end`, `format` | FOB barge basis by location and delivery month |
| `/v1/health` | — (no key needed) | `{"status": "ok"}` |

- `commodity`, `location`, `region`: one name or a comma-separated list, any case (`corn,soybeans`; `Hennepin,STL`).
  Omit to get all.
- `start`, `end`: dates as `YYYY-MM-DD`, inclusive. **With neither, you get the newest sheet only.** For the whole
  history, pass a `start` a year back; anything older than 12 months is not available.
- A parameter the endpoint doesn't take is an error (so a typo never quietly returns everything).

## Fields

| Field | Meaning |
|---|---|
| `date` | the sheet date |
| `delivery_month` | the delivery month, `YYYY-MM` |
| `month_label` | the column as printed on the sheet (`Nov`, `June`, `Spot`) |
| `spot` | `true` for a sheet's prompt ("Spot") column, present on some older sheets |
| `contract` | the CBOT contract the basis is quoted against (`ZCZ26` = Dec 2026 corn). A name only: no futures prices are provided |
| `cif` | CIF NOLA basis, **$/bu** over `contract` (`0.93` = +93 cents) |
| `freight_pct` | barge freight, **percent of tariff** (`725` = 725%) |
| `fob` | FOB barge basis, **$/bu** over `contract`: CIF NOLA less barge freight (tariff rate × freight % / 2000 × bushel weight; corn 56 lb, soybeans and wheat 60 lb) |
| `location`, `river`, `freight_region` | the FOB location, its river reach, and the freight region that prices it |

**No row means no quote.** When a river reach is closed (the upper Mississippi in winter) or a month isn't quoted,
there is no freight and no FOB for it. Nothing is filled in.

JSON responses look like this: `{"dataset": "fob", "units": "...", "start": "...", "end": "...", "window": {...},
"count": 8, "rows": [{...}, ...]}`.

## Examples

```bash
curl -H "X-API-Key: YOUR_KEY" "https://162.243.174.38/v1/fob?commodity=corn&location=Hennepin"
curl -H "X-API-Key: YOUR_KEY" "https://162.243.174.38/v1/cif?start=2025-11-01&format=csv" -o cif.csv
```

```python
import requests
r = requests.get("https://162.243.174.38/v1/freight", params={"region": "IL", "start": "2026-10-01"},
                 headers={"X-API-Key": "YOUR_KEY"}, timeout=60)
r.raise_for_status()
rows = r.json()["rows"]
```

**Excel (Power Query):** Data → Get Data → From Other Sources → Blank Query → Advanced Editor:

```
let
    Source = Csv.Document(Web.Contents("https://162.243.174.38/v1/fob",
        [Query = [commodity = "corn", start = "2025-11-01", format = "csv"],
         Headers = [#"X-API-Key" = "YOUR_KEY"]])),
    Table = Table.PromoteHeaders(Source)
in
    Table
```

If Excel asks how to connect, choose **Anonymous**: the key travels in the header above.

## Limits and errors

- Up to 60 calls a minute and 5,000 a day per key. The data changes once a day, so one pull per day is normally enough.
- `400` a bad parameter (the message says which) · `401` missing or wrong key · `429` too many calls (wait the
  seconds in `Retry-After`) · `503` data briefly unavailable, try again shortly.

Questions: Kolten Postin, John Stewart & Associates.
