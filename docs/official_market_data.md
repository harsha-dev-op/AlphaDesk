# Phase 12 official NSE / NIFTY market-data activation

## Scope and status

Phase 12 activates the existing Phase 8 ingestion and Phase 11 benchmark architecture with official/public data. It adds no migration, indicator, strategy, execution rule, broker path, cache, or feature store. The implementation is ready for review, while NIFTY 500 remains **UNAVAILABLE** because the currently published constituent CSV does not satisfy the strict 500-member EQ identity contract. NIFTY 200 remains **READY**, and all accepted datasets are reported independently.

The fresh official NIFTY 500 artifact verified on 2026-09-20 (SHA-256 `7b90cc2fbfc268cae8eeb95cf1bd80218a0a41f81f0fd1b0ff1ebf64f4dd8bfa`) produced only 498 valid members after strict identity and EQ-series validation. It was not imported, no partial snapshot was persisted, and no replacement data was fabricated.

The activated local dataset covers official NSE cash-market sessions from 2021-01-01 through 2026-09-18, a current NIFTY 200 snapshot dated 2026-09-17, and official NIFTY 200 OHLC over the same session range. Exact live counts are exposed by `GET /api/v1/data-coverage`; they are intentionally not hard-coded into application logic.

## Official source contracts

| Dataset | Official page/report | AlphaDesk parser | PIT rule |
| --- | --- | --- | --- |
| Security master | NSE All Reports, CM MII Security File | `NSE_MII_SECURITY` 1.0.0 | Dated master state only |
| Equity EOD before 2024-07-08 | NSE historical CM Bhavcopy | `NSE_CM_LEGACY_BHAVCOPY` 1.0.0 | RAW trade-date observation |
| Equity EOD from 2024-07-08 | NSE All Reports, CM-UDiFF Common Bhavcopy Final | `NSE_CM_UDIFF_BHAVCOPY` 1.0.0 | RAW trade-date observation |
| NIFTY 200/500 membership | NSE Indices current constituent CSV | `NSE_INDICES_CONSTITUENTS` 1.0.0 | Starts at supplied snapshot date; never backcast |
| NIFTY 200 OHLC | NSE Indices Historical Data | `NSE_INDICES_HISTORICAL_OHLC` 1.0.0 | `available_at` is truthful retrieval/import time |
| Corporate actions | NSE Corporate Actions | `NSE_CORPORATE_ACTIONS` 1.0.0 | No promotion without a trustworthy publication timestamp |
| Calendar holidays | NSE Trading Holidays | `NSE_TRADING_HOLIDAYS` 1.0.0 | Only explicit official rows establish closures |

NSE states on its All Reports page that the old CM bhavcopy was discontinued effective 2024-07-08 in favor of UDiFF. AlphaDesk therefore chooses the format by trade date. Requests remain serial, allowlisted, bounded, and stop on access denial or rate limits.

## Integrity and provenance

- Raw artifacts are checksum-addressed and retained only under Git-ignored `data/nse/raw/` storage.
- Artifact identity includes provider, type, source/range dates, SHA-256, and parser version. Exact re-import is idempotent.
- Accepted rows reference both `source_artifacts` and `data_ingestion_runs`.
- Only CM `EQ` rows enter `daily_prices`; unsupported series and unresolved identities remain explicit issues.
- Differing existing official prices and benchmark rows are conflicts and are never overwritten.
- Current constituents are not treated as historical membership. Research before the first genuine snapshot is refused.
- Official benchmark rows and demo benchmark rows are separated by `source_mode`; no fallback exists.
- The small UUID canonicalization extension in `backtests/fingerprints.py` serializes real provenance UUIDs as their standard strings. Existing payloads without UUID values retain identical canonical bytes and fingerprints.

## Resumable operator commands

From `backend`:

```powershell
.\.venv\Scripts\python.exe -m app.ingestion.nse.cli backfill-year --from 2026-01-01 --to 2026-09-18 --dry-run
.\.venv\Scripts\python.exe -m app.ingestion.nse.cli backfill-year --from 2026-01-01 --to 2026-09-18
.\.venv\Scripts\python.exe -m app.ingestion.nse.cli index-history-backfill --from 2021-01-01 --to 2026-09-18 --dry-run
.\.venv\Scripts\python.exe -m app.ingestion.nse.cli index-history-backfill --from 2021-01-01 --to 2026-09-18
```

Each completed artifact commits independently. A rerun skips already-successful identities. One direct index-history request is limited to 366 calendar days; EOD work is internally bounded to 93-day chunks.

## Readiness interpretation

`READY` is dataset-specific, not a blanket quality claim. NIFTY 200 membership can be ready as a current snapshot while historical membership remains unavailable. Equity coverage is partial until every current NIFTY 200 member has the required research warm-up; newer listings and identity changes remain visible as gaps. Corporate actions and calendar holidays remain unavailable/partial until authoritative artifacts satisfying the PIT contract are imported.

The Data Health workspace displays the activation summary, row/date ranges, and warnings. The Market Regime workspace continues to require an explicit benchmark/source mode and never replaces unavailable OFFICIAL data with DEMO.

## Remaining operator action

Follow [`data/nse/MANUAL_ARTIFACTS_REQUIRED.md`](../data/nse/MANUAL_ARTIFACTS_REQUIRED.md). NIFTY 500 must remain unavailable until a corrected official snapshot dry-runs to exactly 500 uniquely mapped EQ members and is then imported.
