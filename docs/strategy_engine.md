# AlphaDesk Phase 4 strategy research engine

## Purpose and boundary

The strategy engine detects deterministic historical research setups. A matched result means only that a security satisfied every registered rule at the requested historical clock. It is not an investment recommendation and contains no ranking, expected return, target, order, portfolio action, or profitability claim.

```text
Strategies API / read-only workspace
                ↓
         StrategyService
          ↙           ↘
immutable registry   historical index membership
          ↘           ↙
 TechnicalFeatureService.compute_latest_batch
                ↓
 exact-date + available_at gate
                ↓
 typed conditions + deterministic fingerprints
```

Routes translate HTTP errors and schemas only. `StrategyService` resolves the registry definition, validates parameters, resolves historical membership, requests the authoritative strategy technical set, evaluates bounded operators, and builds explanations and fingerprints. No strategy formula is implemented in a route or frontend.

## Registry and versioning

Executable definitions live in the immutable code registry keyed by `(strategy_code, strategy_version)`. Frozen, slotted dataclasses contain direction, research horizon, feature-set dependency, feature codes, EOD timing, parameter definitions and bounds, rules, warm-up requirement, and status. The registry validates duplicate keys, duplicate parameters, unknown features or feature sets, parameter defaults, type compatibility, and boolean equality rules at import time.

A material rule, threshold-default, feature dependency, timing, or parameter-contract change requires a new strategy version. Existing version semantics must not be edited silently.

The existing `strategy_definitions` table remains metadata infrastructure. Its present shape cannot represent the typed executable contract without schema expansion, and the demo placeholder is inactive. Phase 4 therefore creates no competing persisted source of truth, stores no executable expressions, uses no `eval()`, and adds no migration.

## Feature dependencies

`ALPHADESK_CORE_TECHNICAL` v1 remains exactly 36 features and is still the scanner contract. Phase 4 adds `ALPHADESK_STRATEGY_TECHNICAL` v1, containing those same 36 definitions plus two generic price-structure features:

- `PRIOR_HIGH_20` v1 = `max(high[t-20:t])`. It requires 21 observations: 20 completed prior sessions plus the current observation. The current session is explicitly excluded.
- `BREAKOUT_PCT_20` v1 = `close[t] / PRIOR_HIGH_20[t] - 1`. It is null when the prior high is missing or zero.

Both features are calculated by the authoritative full-frame and latest-snapshot technical paths from the same price stream as all other features. RAW evaluation uses raw OHLCV. ADJUSTED evaluation uses the existing availability-gated split/bonus adjustment stream. Existing technical formulas did not change, and `EMA_200` was not added.

## Version 1 strategies

All three definitions are `LONG_ONLY` research descriptions with `EOD_AFTER_CLOSE` timing and default `ADJUSTED` prices. `LONG_ONLY` describes the hypothesized market direction; it does not create an order.

### Momentum Trend v1

- `MOM_3M_63D >= 0.10`
- `MOM_6M_126D >= 0.15`
- `ABOVE_SMA_50 = true`
- `ABOVE_SMA_200 = true`
- `SMA_50_ABOVE_200 = true`
- `VOLUME_RATIO_20 >= 1.00`

Minimum warm-up: 200 observations.

### Breakout 20D v1

- `BREAKOUT_PCT_20 >= 0.00`
- `ABOVE_SMA_50 = true`
- `SMA_20_ABOVE_50 = true`
- `VOLUME_RATIO_20 >= 1.50`
- `CLOSE_LOCATION >= 0.70`
- `ATR_PCT_14 <= 0.08`

The ATR rule is an explicit volatility guard. The breakout comparison is against the prior 20 completed highs and never includes the current high. Minimum warm-up: 50 observations, driven by the longest required rule.

### Mean-Reversion Pullback v1

- `RSI_14 <= 35`
- `DISTANCE_SMA_20 <= -0.03`
- `RET_5D <= -0.03`
- `ABOVE_SMA_200 = true`
- `SMA_50_ABOVE_200 = true`
- `ATR_PCT_14 <= 0.06`

Minimum warm-up: 200 observations.

These thresholds are transparent research defaults, not optimized values or evidence of profitability.

## Parameters and conditions

Each rule references a typed parameter rather than embedding a service-level magic number. Decimal parameters have inclusive minimum/maximum bounds and reject booleans, non-finite values, and out-of-range overrides. Boolean parameters accept only strict booleans. Unknown parameters are rejected. Equivalent decimal forms such as `0.10` and `0.1000` normalize identically for fingerprints.

The bounded operator set is `>`, `>=`, `<`, `<=`, and `=`; boolean rules use equality only. Each result exposes the feature code/version, operator, effective expected value, nullable actual value, pass state, and unit. Missing features are never converted to zero. Any missing required feature yields `NOT_MATCHED` with `INSUFFICIENT_FEATURE_HISTORY` while allowing the rest of the universe evaluation to complete.

Explanations are deterministic templates. They summarize an all-pass result or identify the first failed/unavailable condition. No LLM or advice generator is involved.

## Point-in-time contract

- Historical universe membership uses inclusive `valid_from`/`valid_to` intervals at `observation_date`; current constituents are never substituted.
- `observation_date` cannot exceed the calendar date represented by the timezone-aware `as_of` timestamp.
- Price reads are capped at the earlier of observation date and as-of date.
- An exact observation-date row is mandatory; a prior session is never substituted.
- EOD values require exchange `available_at <= as_of`. A pre-close request therefore cannot use that session's close.
- ADJUSTED reads require corporate-action `available_at <= as_of`, resolve only the latest eligible append-only revision, and apply no action beyond the observation horizon.
- RAW and ADJUSTED policies are explicit in request, response, strategy fingerprint, and dataset fingerprint.
- Appending or changing future prices, future-ex-date actions, or future-available revisions does not change an earlier result or fingerprint.

## Fingerprints

The strategy-definition fingerprint is SHA-256 over canonical JSON containing strategy code/version, normalized effective parameters, ordered rule definitions, required feature versions, required feature-set code/version, and effective adjustment policy.

Each result fingerprint additionally includes observation date, UTC-normalized as-of timestamp, security UUID, dataset/input fingerprint, canonical required-feature state, and matched state. The evaluation fingerprint includes the strategy fingerprint, universe UUID, historical clock, and ordered result fingerprints. Execution timestamps and measured timings are excluded, so equivalent requests are reproducible.

## API

`GET /api/v1/strategies/catalog` returns the registered strategies, versions, descriptions, direction/horizon, feature dependencies, rules, parameter schema/defaults/bounds, default fingerprints, historical universes, latest observation, policies, and research disclaimer.

`POST /api/v1/strategies/evaluate` accepts strategy code/version, universe, observation date, timezone-aware as-of timestamp, RAW/ADJUSTED policy, and optional parameter overrides. It returns every historical member in symbol-ascending order, with matched/not-matched state, all conditions, effective parameters, warnings, provenance, fingerprints, and service timings.

Unknown strategy/universe/version errors return 404. Invalid clocks, types, bounds, or parameters return 422. Internal stack traces are not returned.

## Frontend

`/strategies` is a read-only terminal workspace. It obtains strategy, version, universe, rule, and parameter metadata from the catalog; provides historical date/as-of and price-policy controls; validates obvious numeric bounds; and renders all members with matched state, passed/total rules, required values, condition detail, versions, warnings, and fingerprints. It includes loading, API-error, empty-universe, no-match, insufficient-history, and stale-demo-data states. Backtest and Portfolio Research are separate unlocked consumers; this page still claims no outcome or allocation semantics.

## Tests and PostgreSQL performance

The isolated backend suite has 114 passing tests, including 28 Phase 4 tests covering registry validation and immutability, exact prior-high warm-up/current-row exclusion, full/latest equivalence, setup matches and failures, boundaries and guards, parameter validation/normalization, historical membership, EOD/timezone gates, RAW/ADJUSTED behavior, corporate-action availability/revisions, future-data invariance, API errors, ordering, and the seven-query single-batch contract.

The real PostgreSQL 17.11 benchmark reused the guarded Phase 3.6 `XADB` fixture with 300 sessions per security and `BREAKOUT_20D` v1. Four direct-service runs were taken per size; medians exclude the first run. Every member and all six conditions were serialized.

| Members | Rows | Queries | Warm service median | Warm incl. serialization | API sample | Target | Result |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 50 | 15,000 | 7 | 259.133 ms | 259.940 ms | 243.447 ms | ≤2 s | pass |
| 200 | 60,000 | 19 | 2,758.691 ms | 2,768.083 ms | 2,798.206 ms | ≤5 s | pass |
| 500 | 150,000 | 43 | 6,860.286 ms | 6,885.328 ms | 7,016.105 ms | ≤10 s | pass |

The query count remains `3 + 4 × ceil(member_count / 50)`: index lookup, historical membership, ingestion provenance, then one projected-price, action, calendar-entry, and calendar-range read per bounded chunk. There are no per-security reads. Feature computation—particularly projected history transfer, Decimal calculations, adjustment, and dataset hashing—remains the primary cost; condition evaluation and serialization are small. No cache, snapshot, feature store, or persistence was added. The fixture was removed with the guarded cleanup, leaving zero benchmark rows.

Run the read-only measurement after guarded fixture setup:

```powershell
python -m app.benchmarks.postgres_strategy --runs 4 --sizes 50 200 500
```

The fixture setup and cleanup remain in `app.benchmarks.postgres_scanner_scale`, and each write must be preceded by the project-local PostgreSQL identity probe.

## Phase 5 consumer and remaining boundary

Phase 4 continues to evaluate one EOD observation and return deterministic setup state. Phase 5 consumes these exact immutable definitions through a separate historical-series and backtest-profile layer; it does not move execution assumptions or outcome statistics into the Strategy Registry. Historical/selected feature rows and Phase 4 single-date results are regression-tested for condition equivalence.

Phase 5 now supplies next-open fixed-notional execution, slippage, India delivery-cost assumptions, corporate-action continuity, trade analytics, OOS segmentation, and fingerprints. It still does not implement a capital-constrained portfolio, allocation, optimization, portfolio-valid risk statistics, brokers, paper trading, or live execution. See [backtesting engine specification](backtesting_engine.md).

Phase 6 consumes the same immutable setup definitions through the shared Phase 5 historical preparation path, then applies a separate versioned portfolio policy. Strategy rules, defaults, feature dependencies, EOD timing, and result fingerprints remain unchanged; capital, allocation, cash, exposure, and risk statistics do not enter this registry. See [portfolio/risk engine specification](portfolio_risk_engine.md).
