# NSE EOD ingestion runbook

Phase 8 adds a local/operator workflow for official/public NSE end-of-day data. It writes the existing domain tables, so prices, features, scanner, strategies, backtests, portfolio, and research composition keep using one authoritative data path. It does not add scheduling, background workers, paid feeds, streaming data, or trading.

## Before the first import

From `backend`, safely verify the configured database without exposing its password:

```powershell
.\.venv\Scripts\python -m app.benchmarks.postgres_verify --env-file .env
.\.venv\Scripts\python -m alembic current
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m alembic heads
```

Proceed only when the probe reports PostgreSQL database `alphadesk_dev` and user `alphadesk`. The Phase 8 migration is `7c2e1b8d4a90_nse_source_provenance.py` and follows `4f7a9c2d1e60`.

Inspect the source contracts and current coverage:

```powershell
.\.venv\Scripts\python -m app.ingestion.nse.cli audit
.\.venv\Scripts\python -m app.ingestion.nse.cli status
.\.venv\Scripts\python -m app.ingestion.nse.cli coverage
```

## Safe local-import fallback

If NSE denies or times out automated access, download the official artifact manually in a normal browser and place it directly in `data/nse/inbox/`. Do not extract archives. Imports cannot read arbitrary filesystem locations.

Validate without database writes:

```powershell
.\.venv\Scripts\python -m app.ingestion.nse.cli dry-run NSE_CM_security_14092026.csv.gz --type SECURITY_MASTER --date 2026-09-14
```

Then import the exact same file:

```powershell
.\.venv\Scripts\python -m app.ingestion.nse.cli import NSE_CM_security_14092026.csv.gz --type SECURITY_MASTER --date 2026-09-14
```

Every result reports checksum, normalized fingerprint, rows parsed, inserted, unchanged, rejected, conflicts, warnings, and timings. Successful bytes are retained atomically under ignored checksum-addressed storage. A repeated identical artifact returns its prior identity without domain writes. Different bytes for the same provider/type/date are retained as a conflict and never overwrite historical data.

## Typical sequence

1. Import the dated MII security master before price or constituent files.
2. Dry-run and then import one CM UDiFF bhavcopy.
3. Import current Nifty snapshots with the source's honest as-of date.
4. Import only explicit holiday rows.
5. Inspect corporate actions; expect rows without a publication timestamp to be quarantined.
6. Review CLI coverage and the Data Health page.

Examples:

```powershell
.\.venv\Scripts\python -m app.ingestion.nse.cli security-master --date 2026-09-14 --file NSE_CM_security_14092026.csv.gz --dry-run
.\.venv\Scripts\python -m app.ingestion.nse.cli fetch-eod 2026-09-11 --dry-run
.\.venv\Scripts\python -m app.ingestion.nse.cli import BhavCopy_NSE_CM_0_0_0_20260911_F_0000.csv.zip --type EOD_BHAVCOPY --date 2026-09-11
.\.venv\Scripts\python -m app.ingestion.nse.cli constituents --index NIFTY_200 --as-of 2026-08-31 --file ind_nifty200list.csv --dry-run
.\.venv\Scripts\python -m app.ingestion.nse.cli constituents --index NIFTY_500 --as-of 2026-08-31 --file ind_nifty500list.csv --dry-run
.\.venv\Scripts\python -m app.ingestion.nse.cli corporate-actions corporate_actions.csv --as-of 2026-09-14 --dry-run
.\.venv\Scripts\python -m app.ingestion.nse.cli holidays trading_holidays.csv --as-of 2026-01-01 --dry-run
```

Omit `--dry-run` only after reviewing the plan.

## Incremental refresh and bounded backfill

Fetch one official daily file:

```powershell
.\.venv\Scripts\python -m app.ingestion.nse.cli fetch-eod 2026-09-11
```

Later, deliberately backfill in resumable chunks of at most 93 calendar days:

```powershell
.\.venv\Scripts\python -m app.ingestion.nse.cli backfill --from 2026-01-01 --to 2026-03-31 --dry-run
.\.venv\Scripts\python -m app.ingestion.nse.cli backfill --from 2026-01-01 --to 2026-03-31
```

Do not launch a multi-year run. Use successive small date windows, inspect each result, and stop on provider rate limits or access denial. A missing download is unknown—not proof of a holiday or a no-trade session.

## Acceptance policy

- Only Capital Market `EQ` rows are accepted in Phase 8. Other series are explicitly counted and reported.
- Symbols and ISINs must pass format checks and resolve to one security identity. Symbol and ISIN disagreement is a conflict, not an update.
- UDiFF trade date must equal the artifact's supplied source date. OHLC is strictly positive, `high >= low`, open/close lie within the range, volume is a non-negative 32-bit integer, and traded value is finite/non-negative when present.
- Raw source OHLCV enters `daily_prices` unchanged with source `NSE_CM_UDIFF`. The existing adjustment service remains the only ADJUSTED view.
- Existing identical official prices count as unchanged. Any differing existing price—including a demo collision—is a `HISTORICAL_DATA_CONFLICT`; it is never silently overwritten.
- A price artifact confirms that date as a trading session. An explicit holiday conflicting with a bhavcopy is rejected and not reclassified.
- Nifty 200/500 files must resolve exactly 200/500 unique members. A current snapshot starts at the explicit as-of date and is never backcast.

Parser-level invalid rows may yield `PARTIAL` with structured issue counts while valid independent rows commit in the same artifact transaction. Architecture or persistence failure rolls the entire domain write back and records a sanitized failed-artifact summary.

## Coverage and troubleshooting

Read-only APIs:

- `GET /api/v1/data-sources` — audited source definitions and latest state by artifact type.
- `GET /api/v1/data-coverage` — mode, dates, counts, current index snapshots, artifacts, issues, and warnings.
- `GET /api/v1/data-quality/status` — OHLC, duplicate, session, calendar, freshness, artifact, membership, and corporate-action PIT checks.

Common outcomes:

- `Official source denied automated access` — use the local inbox workflow; do not retry with bypass techniques.
- `Official artifact schema is missing required columns` — retain the file as failed provenance, compare it with the audited official format, then version the parser deliberately.
- `SOURCE_ARTIFACT_REVISION` — different bytes already exist for that source identity; inspect rather than overwrite.
- `HISTORICAL_MEMBERSHIP_COVERAGE_INCOMPLETE` — the requested real-index start predates imported snapshots. Obtain a trustworthy historical source or use a covered range.
- `AVAILABILITY_TIMESTAMP_UNKNOWN` — action remains quarantined and cannot affect adjusted prices.

## Verification and benchmark

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m alembic check
.\.venv\Scripts\python -m app.benchmarks.postgres_nse_ingestion
```

The benchmark uses a guarded synthetic namespace for one 2,000-row artifact and 30 × 500 price rows. It measures parse, persistence, total time, SELECT count, and SQL statement count, then deletes all benchmark securities, prices, calendar rows, artifacts, issues, and runs. It never calls NSE servers. Recovery cleanup is available with `--cleanup-only` after database identity verification.
