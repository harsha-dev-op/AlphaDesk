# AlphaDesk Phase 4 architecture

## Request flow

```text
React/Vite UI → typed API client → FastAPI routes → strategy/scanner/technical services → repositories → SQLAlchemy → PostgreSQL
                                      ↘ immutable registries / quality / calendar / adjustments
```

Routes translate HTTP concerns only. Services own adjustment, calendar, and quality behavior. Repositories own point-in-time query semantics. Provider implementations normalize vendor payloads before ORM persistence.

## Bias controls

- **Look-ahead:** `fundamental_reports.effective_date` is the first date a specific version is knowable. Queries require both `reported_date` and `effective_date` to be on or before the simulation date.
- **Survivorship:** `index_memberships` uses inclusive `valid_from` and `valid_to` intervals. An open-ended membership has `valid_to = NULL`.
- **Corporate actions:** `daily_prices` is raw and unique by security/session. Actions require `available_at <= as_of`; only the latest eligible append-only revision enters the derived split/bonus response.
- **Missing data:** calendar gaps, stale coverage, impossible OHLCV, and duplicates return named health checks. They are never converted into a trading decision.
- **Reproducibility:** ingestion runs record a `dataset_code` and `dataset_version`; strategies are keyed by stable UUID plus code/version.
- **Technical features:** immutable code definitions and feature sets are computed on demand over the full available prefix. Each output carries the feature-set version, data provenance, adjustment policy, compute time, and exchange-close availability time.
- **Adjusted point-in-time state:** feature results are snapshotted per corporate-action regime. Future split or bonus rows may rebase later calculations but cannot change an earlier observation's emitted values.
- **Market scans:** historical membership is resolved at the requested observation date. Computation is capped at that date and its timezone-aware as-of timestamp, requires an exact-date feature row, and excludes any row whose `available_at` is later than scanner `as_of`.
- **Strategy setups:** immutable code definitions consume the strategy-support feature set through the same batched service. Every historical member receives typed matched/not-matched conditions; missing required values fail explicitly rather than becoming zero.

## Corporate-action conventions

- A `STOCK_SPLIT` ratio of 2:1 stores numerator `2`, denominator `1`; pre-ex-date prices receive factor `1/2` and volume receives factor `2`.
- A `BONUS` ratio of 1:1 stores numerator `1`, denominator `1`; pre-ex-date prices receive factor `1/(1+1)` and volume receives factor `2`.
- `source_published_at` is used only when trustworthy; otherwise `available_at` equals local `ingested_at`. `announcement_date` remains informational. The fictional legacy/demo backfill uses announcement-date end-of-day IST only as an explicitly non-truth deterministic fallback.
- Corrections append a new row whose `supersedes_action_id` points to the prior row. Repository reads gate by availability, resolve the latest eligible row, then apply the ex-date horizon. Original rows remain UUID-addressable and future revisions cannot leak backward.
- Cash dividends, rights, mergers, and demergers are preserved as events but deliberately do not receive a guessed price adjustment in Phase 1. A provider-specific policy and validation suite must precede those calculations.

## Phase 2 technical dependency flow

```text
feature API → TechnicalFeatureService → SecurityRepository + TradingCalendarRepository
                         ↓                         ↓
              immutable v1 registry     raw OHLCV / dated actions
                         ↓
              Decimal calculators → typed response + provenance
```

No Phase 2 migration is required: feature contracts live in versioned code, calculations are dynamic, and the existing ingestion-run records supply dataset provenance. See [technical feature specification](technical_features.md).

## Phase 2.5 batch/latest path

Future scanner-style callers can use `TechnicalFeatureService.compute_latest_batch`. It reads ordered histories and corporate actions in bounded 50-security chunks, loads ingestion metadata once, batches calendar lookup, and delegates formula semantics to the version-1 calculator layer. The specialized latest calculator emits only the final feature row but intentionally retains the complete prefix required by the established EMA seed and recursive RSI/ATR conventions. Single and batch values are regression-tested for exact equivalence.

Price batches project only fields required by the technical engine into lightweight read records; action reads use `(security_id, available_at)`. Calendar reads remain batched. See [performance baseline](performance_baseline.md) and [Phase 3.6 scale validation](postgres_scanner_scale_validation.md).

## Phase 3 scanner flow

```text
metadata API ───────────────→ versioned technical registry

scan API → ScannerService → historical index membership
                  ↓
        compute_latest_batch(observation_date, as_of)
                  ↓
        exact-date/availability gate → typed AND predicates
                  ↓
        deterministic rows + provenance + scan fingerprint
```

`ScannerService` owns request validation, universe resolution, flat AND predicate evaluation, result ordering, and the request/member/dataset scan fingerprint. It delegates all indicator semantics to the existing technical service and performs bounded 50-security batch reads. The API layer only maps typed requests, responses, and domain errors; the frontend obtains all selectable feature definitions from the metadata endpoint.

Scans remain dynamically computed and read-only. Phase 3.6 adds only the corporate-action availability/revision migration and a measured projected price read; no persistent snapshot or cache was introduced. See [market scanner specification](market_scanner.md), [corporate-action contract](corporate_action_point_in_time.md), and [PostgreSQL scale validation](postgres_scanner_scale_validation.md).

## Phase 4 strategy flow

```text
strategy catalog API ─────────→ immutable strategy registry

evaluation API → StrategyService → historical index membership
                       ↓
     ALPHADESK_STRATEGY_TECHNICAL v1
                       ↓
       compute_latest_batch(observation_date, as_of)
                       ↓
          exact-date/availability gate
                       ↓
 typed conditions → deterministic explanations + fingerprints
```

The strategy set includes Core v1 unchanged plus `PRIOR_HIGH_20` and `BREAKOUT_PCT_20`. The code registry is the authoritative executable source. The existing `strategy_definitions` table stays metadata-only because its compact schema cannot model typed rules and parameters without unnecessary migration complexity. Evaluations are dynamic and unpersisted. See [strategy engine specification](strategy_engine.md).

## Boundary for later phases

No order, recommendation, broker, portfolio, backtest, ranking, ML, or alert execution exists. The scanner and strategy engine are read-only. `strategy_definitions` is metadata only. The recommended next boundary is a point-in-time Indian-market backtesting engine that consumes immutable strategy definitions; Phase 4 does not implement it.
