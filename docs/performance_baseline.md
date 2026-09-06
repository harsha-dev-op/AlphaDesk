# AlphaDesk Phase 2.5 performance baseline

> Phase 3.5 PostgreSQL validation is complete for the legitimate three-member demo universe. See [the Phase 3.5 validation report](postgres_scanner_validation.md) for migrations, seed counts, API smoke results, PostgreSQL timings, profiling, and the architecture decision.

> Phase 3.6 adds a representative 300-session PostgreSQL fixture and measured 1/10/50/200/500-member scans. See [the Phase 3.6 scale report](postgres_scanner_scale_validation.md). A narrow price projection brought the 500-member warm API median to 7.040 seconds, so the decision is B: small hardening is sufficient and persistent snapshots remain deferred.

This report records one local engineering run; it is not a universal latency guarantee. The workload is deterministic, project-local, uses no network or external data, and keeps `ALPHADESK_CORE_TECHNICAL` version `1` semantics unchanged.

## Environment and method

- Measured 2026-09-06 on Windows 11 (`10.0.26200`) with CPython 3.12.14.
- Each synthetic security has 2,500 weekday observations, approximating ten years of trading-session-scale history.
- OHLCV and traded value use deterministic `Decimal` inputs. Security-specific offsets make histories independent.
- The ADJUSTED workload includes a 2:1 split at row 800 and a 1:1 bonus at row 1,600. RAW and ADJUSTED modes are both executable.
- All 36 version-1 features are calculated. Timings use `perf_counter`; fingerprints are SHA-256 over ordered output values.
- Peak Python allocation was measured separately with `tracemalloc`, because tracing materially slows Decimal-heavy code.
- The harness does not run during ordinary tests.

Reproduce from `backend`:

```powershell
python -m app.benchmarks.technical_features --securities 1 10 50 200 --rows 2500 --mode full --adjustment-policy ADJUSTED
python -m app.benchmarks.technical_features --securities 1 10 50 --rows 2500 --mode latest-baseline --adjustment-policy ADJUSTED
python -m app.benchmarks.technical_features --securities 1 10 50 200 500 --rows 2500 --mode latest --adjustment-policy ADJUSTED
python -m app.benchmarks.technical_features --securities 1 10 50 --rows 2500 --mode latest --adjustment-policy RAW
python -m app.benchmarks.technical_features --securities 1 --rows 2500 --mode latest --adjustment-policy ADJUSTED --memory
```

`full` computes and fingerprints every historical feature row. `latest-baseline` records the pre-optimization behavior: compute the entire frame and consume only its final row. `latest` uses the optimized latest-snapshot calculator while retaining the complete prefix required by the documented EMA seed and other recursive features.

## Full-history ADJUSTED baseline

| Securities | Rows | Feature values | Input prep | Adjustment | Calculation | Fingerprint | Orchestration | Total | Throughput |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 2,500 | 90,000 | 0.015 s | 0.014 s | 0.194 s | 0.024 s | <0.001 s | 0.248 s | 10,096 rows/s |
| 10 | 25,000 | 900,000 | 0.163 s | 0.141 s | 2.247 s | 0.288 s | <0.001 s | 2.839 s | 8,806 rows/s |
| 50 | 125,000 | 4,500,000 | 2.034 s | 1.877 s | 25.205 s | 3.552 s | 0.001 s | 32.669 s | 3,826 rows/s |
| 200 | 500,000 | 18,000,000 | 8.107 s | 7.881 s | 101.646 s | 14.286 s | 0.002 s | 131.921 s | 3,790 rows/s |

The 500-security full-history run was intentionally not executed. The measured 200-security case already took 132 seconds and materialized 18 million feature values; a 500-security run would materialize 45 million values and was not necessary to characterize the linear scaling limit. This is a safety limit, not a fabricated 500-security result.

## Latest-snapshot results

Pre-optimization, latest requests still built the entire frame:

| Securities | Input prep | Adjustment | Calculation | Fingerprint | Total |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.015 s | 0.014 s | 0.195 s | <0.001 s | 0.224 s |
| 10 | 0.167 s | 0.141 s | 1.961 s | 0.001 s | 2.269 s |
| 50 | 2.056 s | 1.809 s | 25.150 s | 0.004 s | 29.019 s |

The optimized ADJUSTED path produced only 36 values per security:

| Securities | Rows read | Feature values | Input prep | Adjustment | Calculation | Fingerprint | Orchestration | Total | Throughput |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 2,500 | 36 | 0.017 s | 0.014 s | 0.012 s | <0.001 s | <0.001 s | 0.042 s | 58,894 rows/s |
| 10 | 25,000 | 360 | 0.157 s | 0.144 s | 0.118 s | <0.001 s | <0.001 s | 0.419 s | 59,619 rows/s |
| 50 | 125,000 | 1,800 | 0.947 s | 0.889 s | 0.710 s | 0.001 s | 0.001 s | 2.548 s | 49,059 rows/s |
| 200 | 500,000 | 7,200 | 9.832 s | 9.441 s | 7.206 s | 0.013 s | 0.001 s | 26.493 s | 18,873 rows/s |
| 500 | 1,250,000 | 18,000 | 20.515 s | 19.598 s | 15.243 s | 0.026 s | 0.003 s | 55.385 s | 22,569 rows/s |

The equivalent RAW latest run completed 1/10/50 securities in 0.034/0.305/1.517 seconds. RAW and ADJUSTED output digests differ once corporate actions are in scope, as expected.

At 1, 10, and 50 securities, latest total time improved by 5.3×, 5.4×, and 11.4× respectively versus the recorded baseline. Latest feature calculation alone improved from 25.150 seconds to 0.710 seconds at 50 securities (35.4×). Separately instrumented one-security peak Python allocation fell from 15.227 MiB for a full frame to 5.050 MiB for latest-only output. Tracemalloc timings are excluded from the timing tables.

## Profile findings and implemented optimization

A `cProfile` run of the 10-security latest baseline recorded about 7.06 million calls. Feature calculation consumed 4.40 seconds under instrumentation. Cumulative calculator time was led by volatility at 2.55 seconds (58% of calculator time), then trend at 0.90 seconds (20%) and liquidity at 0.54 seconds (12%). Repeated rolling-window `sum`/generator work and construction of all historical row dictionaries dominated; fingerprinting and orchestration were negligible.

Each feature family now exposes shared authoritative primitives used by both the full-history and latest orchestration paths; no formula is duplicated. The latest calculator materializes only the newest row. It retains all 2,500 points where required so the existing SMA, momentum, Wilder RSI, Wilder ATR, volatility, and first-window EMA seed conventions remain exact. The batch service groups ordered price and corporate-action reads, loads immutable ingestion metadata once, batches calendar reads, and defaults to 50 securities per bounded chunk.

Twenty new Phase 2.5 test cases compare the optimized row exactly with the authoritative final row across warm-up boundaries and 2,500-row RAW/ADJUSTED histories. They also cover multi-security isolation, future-row invariance, session-close availability, feature-set version `1`, dataset metadata, batch/single equivalence, deterministic benchmark fingerprints, and a five-SELECT one-chunk query budget. The complete backend suite is 47 passing tests.

## Database and persistence decision

Existing composite indexes already match the new ordered access patterns: `(security_id, trading_date)` for prices, `(security_id, ex_date)` for actions, and `(exchange, is_trading_day, trading_date)` for calendars. No speculative index or migration was added.

Recommendation: do not add Redis, workers, or a feature store in Phase 2.5. Dynamic latest calculation is suitable for small and medium universes, but the measured 26.5-second/55.4-second 200/500-security synthetic pipelines are not yet interactive. Phase 3 should first measure end-to-end PostgreSQL retrieval with the new batch interface. If the scanner latency target is not met, add a database-backed, version-keyed latest-snapshot materialization keyed by security, observation/availability time, feature-set version, adjustment policy, and dataset fingerprint. Historical full-frame materialization is not justified by this evidence.

## PostgreSQL verification status

The local server at `localhost:5432` accepted TCP/PostgreSQL readiness checks. No `.env` exists in the project; only placeholder `.env.example` values are present. A sanitized probe using `backend/.env.example` reached the server but returned `AUTHENTICATION_FAILED` for database/user `alphadesk`/`alphadesk`. In accordance with the project safety boundary, no credential search, password reset, auth configuration change, database creation, migration, seed, or PostgreSQL API smoke test was attempted.

As a separate portability regression check, the existing Alembic head completed against a disposable project-local SQLite database and created the expected migration-version table plus nine application tables. The artifact was removed. This confirms the migration still executes in the isolated test dialect; it does not claim PostgreSQL verification.

The remaining manual action is to create or select an isolated `alphadesk_dev` database and login using the user's trusted PostgreSQL administration workflow, then place its valid SQLAlchemy URL in untracked `backend/.env`. Run the documented probe, migration, seed, and API checks in the README afterward. No password should be committed or printed.

## Limitations

- Benchmark input preparation is Python synthetic generation, not PostgreSQL I/O; PostgreSQL end-to-end latency remains unmeasured until valid project-local credentials exist.
- Results are one local run and include normal interpreter, allocator, scheduler, and thermal variability.
- The current EMA convention depends on its full historical prefix, so latest calculation deliberately does not truncate history to 200 rows.
- The benchmark is sequential and does not model concurrent API clients or serialization over HTTP.
- No scanner, ranking, strategy, signal, or visible frontend work was added.
