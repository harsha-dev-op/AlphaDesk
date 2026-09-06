# AlphaDesk Phase 3.5 PostgreSQL scanner validation

> Superseded as the scale decision by [Phase 3.6 PostgreSQL scanner validation](postgres_scanner_scale_validation.md). Phase 3.6 also closes the corporate-action availability/revision limitation documented below; the historical Phase 3.5 measurements remain unchanged.

## Status and environment

Phase 3.5 runtime validation completed on 2026-09-06 against the explicitly configured isolated development database. The safe probe authenticated without exposing the password or database URL.

| Field | Verified value |
| --- | --- |
| Status | `CONNECTED` |
| Driver | `postgresql+psycopg` |
| Host / port | `localhost:5432` |
| Database | `alphadesk_dev` |
| User | `alphadesk` |
| PostgreSQL | 17.11, 64-bit Windows build |
| Python | CPython 3.12.14 |

The Alembic starting state was an empty revision. `alembic upgrade head` applied the existing transactional initial migration, and the final revision is `8b85071c8e5c`. `alembic check` reported `No new upgrade operations detected`.

Runtime inspection found all ten expected tables including `alembic_version`. PostgreSQL returned mapped numerics as Python `Decimal`, booleans as `bool`, and timestamp values with timezone information. Nullable date/time behavior was preserved. The schema contains the expected indexes, five foreign keys, and uniqueness constraints; there are no JSON columns in the current model.

## Deterministic demo seed

The existing seed ran only after the connection was reverified as `alphadesk_dev`/`alphadesk`. It completed successfully and a second identity-gated run inserted zero rows, confirming its idempotent guard.

| Table | Rows |
| --- | ---: |
| Securities | 4 |
| Daily prices | 218 |
| Indices | 1 |
| Index memberships | 4 |
| Corporate actions | 2 |
| Fundamental reports | 8 |
| Trading-calendar rows | 90 |
| Data-ingestion runs | 1 |
| Inactive strategy placeholder | 1 |

The data is fictional, uses dated memberships and actions, and retains the established raw-price/non-destructive-adjustment contract.

## PostgreSQL API smoke tests

The reusable read-only harness is `python -m app.benchmarks.postgres_scanner --mode smoke`. It verifies the database identity before using the real SQLAlchemy engine and exercises the FastAPI application in-process.

- Phase 1 passed: health, security list, `ALPHAIND` detail, 62 `ALPHAIND` price rows, index list, historical memberships, and data-quality status all returned HTTP 200. Data quality was `WARNING` only because the fictional data intentionally ends in March 2025.
- Historical membership was exact: 2025-02-14 returned `ALPHAIND`, `BETATECH`, and `OLDCO`; 2025-02-17 returned `ALPHAIND` and `GAMMAFIN`.
- Phase 2 passed: 36 registry features, the versioned core feature set, and RAW/ADJUSTED feature queries returned HTTP 200. On the `ALPHAIND` split date, RAW `RET_1D` was approximately -0.497242 and ADJUSTED `RET_1D` approximately 0.005516.
- Phase 3 passed: scanner metadata, simple, three-filter, historical-universe, repeated deterministic, RAW, and ADJUSTED scans all returned HTTP 200.
- Scanner rows matched the exact observation date, satisfied `available_at <= as_of`, carried valid 64-character fingerprints and feature version `1`, and were symbol-sorted.
- The 2025-01-31 scan excluded 90 later price rows. Both database actions had future ex-dates at that point, and zero actions were eligible for the historical calculation.
- Repeating the same request returned the same scan fingerprint.

## PostgreSQL scanner benchmark

Run `python -m app.benchmarks.postgres_scanner --mode benchmark --runs 4`. The benchmark is read-only, checks database identity, and uses the authoritative `ALPHADESK_CORE_TECHNICAL` version `1` registry. It scans the largest legitimate seeded universe with enough history: three members on 2025-01-31, 66 total price rows, all 36 computed features, three registered filter conditions, and ADJUSTED prices.

The deterministic seed cannot legitimately provide 1, 10, 50, 200, or 500-member cases: the warmed-up historical universe contains three members at most. No extra records were fabricated. Consequently, the 50-security ≤2 s, 200-security ≤5 s, and 500-security ≤10 s engineering targets remain untested rather than extrapolated.

### Individual service runs

| Run | Service | Serialization | Total with serialization | API request | Queries | Results |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 14.070 ms | 0.111 ms | 14.180 ms | 24.489 ms | 7 | 3 |
| 2 | 8.291 ms | 0.069 ms | 8.360 ms | 9.700 ms | 7 | 3 |
| 3 | 7.876 ms | 0.061 ms | 7.937 ms | 9.557 ms | 7 | 3 |
| 4 | 7.778 ms | 0.059 ms | 7.837 ms | 15.296 ms | 7 | 3 |

The first run is process-cold only; PostgreSQL and operating-system caches were not cleared. Warm service median was **7.876 ms** with a 7.778–8.291 ms range. Warm median including serialization was **7.937 ms**. Warm in-process FastAPI median was **9.700 ms** with a 9.557–15.296 ms range. Every run produced the same scan fingerprint.

### Warm median timing breakdown

| Stage | Median |
| --- | ---: |
| Universe lookup | 1.049 ms |
| Historical membership | 0.993 ms |
| Price retrieval total | 1.718 ms |
| Price transfer/ORM/grouping residual | 1.219 ms |
| Corporate-action retrieval total | 0.784 ms |
| Action transfer/ORM/grouping residual | 0.458 ms |
| Calendar-entry lookup | 0.622 ms |
| Trading-calendar range lookup | 0.605 ms |
| Adjustment preparation | 0.313 ms |
| Decimal-to-feature-point conversion | 0.088 ms |
| Technical calculation | 0.358 ms |
| Dataset fingerprint | 0.210 ms |
| Predicate evaluation | 0.016 ms |
| Response construction | 0.048 ms |
| Pydantic serialization | 0.061 ms |

SQLAlchemy cursor events measure statement execution; repository residuals combine network row transfer, ORM construction, and Python grouping and cannot be separated accurately without intrusive instrumentation. The warm per-table cursor medians were approximately 0.368 ms for index lookup, 0.395 ms for membership, 0.306 ms for ingestion metadata, 0.406 ms for prices, 0.349 ms for corporate actions, and 0.519 ms combined for the two calendar reads.

The query pattern was exactly seven SELECTs, matching `3 + 4 × ceil(3 / 50)`: index, membership, ingestion metadata, prices, actions, calendar entries, and trading-day range. No N+1, repeated metadata lookup, duplicate action read, or duplicate security read was observed.

Lightweight `cProfile` evidence agrees with the stage instrumentation. In the profiled run, the scanner took 12.688 ms cumulatively, `compute_latest_batch` 8.329 ms, SQLAlchemy session execution 6.565 ms, and psycopg waiting about 3.294 ms. At this small size, fixed database/ORM round-trip overhead is the primary measured cost; feature calculation, adjustment, predicates, fingerprinting, and serialization are individually sub-millisecond.

## Architecture decision

**A. CURRENT DYNAMIC ARCHITECTURE IS SUFFICIENT** for the only legitimately measured PostgreSQL workload: the seeded three-member research universe completes in about 8 ms warm service time and 10 ms warm API time.

This result does not establish capacity at 50, 200, or 500 securities. Persistent snapshots remain unjustified until a representative, legitimately sourced PostgreSQL dataset measures those target sizes. The earlier large synthetic SQLite results remain a warning about full-history scaling, not a substitute for PostgreSQL evidence. No cache, feature store, migration, or snapshot was added in Phase 3.5.

## Corporate-action historical availability (Phase 3.5 finding; resolved in Phase 3.6)

At the time of this Phase 3.5 run, the model stored `announcement_date`, `ex_date`, and `ingested_at`, but did not apply an independent, revision-aware vendor publication timestamp as a historical gate. Adjustment queries capped actions by `ex_date <= observation/as-of horizon`, and factors affected only prices before the ex-date. The PostgreSQL smoke run confirmed that future ex-date actions were excluded.

A late-loaded, corrected, or backfilled action with an old ex-date can still change a rerun of an older ADJUSTED scan because `announcement_date` and `ingested_at` are not used as availability gates. Fingerprints reveal that the inputs changed, but cannot reconstruct what was knowable at the earlier instant.

Phase 3.6 resolved this finding with `source_published_at`, `available_at`, `supersedes_action_id`, a deliberate legacy backfill, eligible-revision resolution, and revision-aware fingerprints. See [the current contract](corporate_action_point_in_time.md).

## Remaining limitations and next phase

- The demo universe is too small to validate the 50/200/500 targets.
- Process-cold measurements do not clear PostgreSQL or OS caches.
- Row transfer, ORM materialization, and grouping are reported as a combined residual.
- Historical price corrections remain outside this phase; corporate-action revisions are now gated by availability time in Phase 3.6.
- The two Starlette/httpx deprecation warnings remain unrelated and non-failing.

The next phase should be a bounded point-in-time data hardening phase: implement corporate-action availability/revision semantics before serious backtesting, and obtain a legitimate representative PostgreSQL history for 50/200/500 scanner validation. Reconsider version-keyed latest-feature snapshots only if that evidence misses the engineering targets. Do not add strategies, signals, ranking, or execution as part of this hardening work.
