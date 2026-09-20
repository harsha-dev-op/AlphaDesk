# Phase 12 manual official artifacts required

Status: **NIFTY 500 UNAVAILABLE — CURRENT OFFICIAL SNAPSHOT FAILED STRICT VALIDATION**

All files must come directly from the named official page, remain inside `data/nse/inbox/`, and be dry-run before import. Do not rename third-party data to match these filenames.

## 1. Unavailable pending a corrected current NIFTY 500 constituent snapshot

- Official page: https://www.niftyindices.com/indices/equity/broad-based-indices/nifty-500
- Report/download: current NIFTY 500 constituent CSV
- Required date: the actual download/publication snapshot date; use that date as `YYYY-MM-DD`
- Expected file: CSV, normally `ind_nifty500list.csv`
- Destination: `data/nse/inbox/ind_nifty500list.csv`

The fresh official file downloaded and dry-run on 2026-09-20 was not imported. Its SHA-256 is `7b90cc2fbfc268cae8eeb95cf1bd80218a0a41f81f0fd1b0ff1ebf64f4dd8bfa`. Strict validation found malformed `DUMMYHEG:EQ`, non-EQ `HEG:BE` and `HFCL:BE`, unresolved `HSCL:EQ`, and only 498 valid members instead of 500. No partial membership was persisted and no replacement data was fabricated. Keep NIFTY 500 unavailable until the official publisher supplies a corrected snapshot, then validate it:

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.ingestion.nse.cli constituents --index NIFTY_500 --as-of YYYY-MM-DD --file ind_nifty500list.csv --dry-run
.\.venv\Scripts\python.exe -m app.ingestion.nse.cli constituents --index NIFTY_500 --as-of YYYY-MM-DD --file ind_nifty500list.csv
```

Import only when dry-run reports exactly 500 mapped members, zero unmapped identities, zero conflicts, and zero rejected rows.

## 2. Optional for full PIT adjustment coverage: corporate actions

- Official page: https://www.nseindia.com/companies-listing/corporate-filings-actions
- Report/download: official Corporate Actions CSV with source publication timestamp fields
- Required range: 2021-01-01 through the latest completed session
- Expected file: CSV
- Destination: `data/nse/inbox/corporate_actions.csv`

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.ingestion.nse.cli corporate-actions corporate_actions.csv --as-of YYYY-MM-DD --dry-run
.\.venv\Scripts\python.exe -m app.ingestion.nse.cli corporate-actions corporate_actions.csv --as-of YYYY-MM-DD
```

Rows lacking a trustworthy timezone-aware source publication timestamp will be quarantined and cannot affect ADJUSTED point-in-time data. Do not fabricate timestamps.

## 3. Optional for explicit closure coverage: trading holidays

- Official page: https://www.nseindia.com/resources/exchange-communication-holidays
- Report/download: official capital-market trading-holiday CSV
- Required range: 2021 through 2026 (one official file per published year if necessary)
- Expected file: CSV
- Destination: `data/nse/inbox/trading_holidays_YYYY.csv`

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.ingestion.nse.cli holidays trading_holidays_YYYY.csv --as-of YYYY-01-01 --dry-run
.\.venv\Scripts\python.exe -m app.ingestion.nse.cli holidays trading_holidays_YYYY.csv --as-of YYYY-01-01
```

Imported EOD artifacts already confirm actual trading sessions, including special sessions. Missing dates are not automatically labeled holidays.
