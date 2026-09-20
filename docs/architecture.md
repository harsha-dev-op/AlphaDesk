# AlphaDesk Phase 12 architecture

## Request flow

```text
React/Vite UI → typed API client → FastAPI routes → regime/historical research/composition/portfolio/backtest/strategy/scanner/technical services → repositories → SQLAlchemy → PostgreSQL
                                      ↘ immutable registries / quality / calendar / adjustments
```

Routes translate HTTP concerns only. Services own adjustment, calendar, and quality behavior. Repositories own point-in-time query semantics. Provider implementations normalize vendor payloads before ORM persistence.

## Bias controls

- **Look-ahead:** `fundamental_reports.effective_date` is the first date a specific version is knowable. Queries require both `reported_date` and `effective_date` to be on or before the simulation date.
- **Survivorship:** `index_memberships` uses inclusive `valid_from` and `valid_to` intervals. An open-ended membership has `valid_to = NULL`.
- **Corporate actions:** `daily_prices` is raw and unique by security/session. Actions require `available_at <= as_of`; only the latest eligible append-only revision enters the derived split/bonus response.
- **Missing data:** calendar gaps, stale coverage, impossible OHLCV, and duplicates return named health checks. They are never converted into a trading decision.
- **Reproducibility:** ingestion runs record a `dataset_code` and `dataset_version`; strategies are keyed by stable UUID plus code/version.
- **Source lineage:** each official artifact has a provider/type/date/SHA-256 identity, parser version, bounded issue register, and local ignored storage key. Accepted domain rows reference both artifact and run.
- **Real/demo honesty:** source-bearing domain rows carry coarse `DEMO`, `OFFICIAL_NSE_PUBLIC`, or `UNKNOWN` origin; coverage derives `MIXED` when both coexist.
- **Current constituents:** free Nifty 200/500 lists create intervals only from an explicit snapshot date. Requests before the first official snapshot fail with `HISTORICAL_MEMBERSHIP_COVERAGE_INCOMPLETE`.
- **Technical features:** immutable code definitions and feature sets are computed on demand over the full available prefix. Each output carries the feature-set version, data provenance, adjustment policy, compute time, and exchange-close availability time.
- **Adjusted point-in-time state:** feature results are snapshotted per corporate-action regime. Future split or bonus rows may rebase later calculations but cannot change an earlier observation's emitted values.
- **Market scans:** historical membership is resolved at the requested observation date. Computation is capped at that date and its timezone-aware as-of timestamp, requires an exact-date feature row, and excludes any row whose `available_at` is later than scanner `as_of`.
- **Strategy setups:** immutable code definitions consume the strategy-support feature set through the same batched service. Every historical member receives typed matched/not-matched conditions; missing required values fail explicitly rather than becoming zero.

## Corporate-action conventions

- A `STOCK_SPLIT` ratio of 2:1 stores numerator `2`, denominator `1`; pre-ex-date prices receive factor `1/2` and volume receives factor `2`.
- A `BONUS` ratio of 1:1 stores numerator `1`, denominator `1`; pre-ex-date prices receive factor `1/(1+1)` and volume receives factor `2`.
- `source_published_at` is used only when trustworthy. Phase 8 official/public imports without it are quarantined and never fabricate `available_at`; the pre-existing fictional legacy/demo backfill remains the only path using its explicitly non-truth deterministic fallback.
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

## Phase 5 backtest flow

```text
backtest metadata API ─────────→ strategy/profile/cost registries

run API → BacktestService → overlapping historical membership intervals
              ↓
     batched projected price/action reads + calendar range
              ↓
 HistoricalTechnicalSeriesService → authoritative selected feature formulas
              ↓
 immutable Phase 4 rule evaluation → deterministic SetupEvents
              ↓
 NEXT_OPEN_FIXED_HOLD v1 simulator → India cash-delivery costs
              ↓
 full-trade analytics + yearly/OOS rows + bounded preview + fingerprints
```

Decision data is capped at each exchange-close timestamp; outcome data enters only after a matched setup. Feature rows are computed once per security and action-knowledge regime, never by issuing per-date Phase 4 evaluations. Entry and exit prices are RAW even when features are ADJUSTED. Historical membership controls signal eligibility but does not force an open trade to exit.

Backtest profiles and cost models are separate frozen registries, so execution assumptions do not mutate Phase 4 strategy meaning. Results are calculated on demand; no persistence table or migration is added. See [backtesting engine specification](backtesting_engine.md).

## Boundary for later phases

No order, recommendation, broker, ranking, optimization, ML, or alert execution exists. Scanner, strategy, backtest, portfolio, and composition APIs remain research calculations. `strategy_definitions` remains metadata only. Saved experiments preserve research provenance but do not connect composition to execution or allocation.
## Phase 6 portfolio and risk boundary

Phase 6 adds an on-demand `PortfolioService` above the unchanged Phase 4 strategy and Phase 5 historical execution layers. `BacktestService.prepare_research` is the single reusable path for historical membership, calendar, feature-series, setup, and dataset construction; the Phase 5 backtest service and Phase 6 portfolio service both consume it. Portfolio allocation does not enter the Strategy Registry, and the Phase 5 independent-trade API keeps its original semantics and deterministic fingerprints.

The frozen `LONG_ONLY_EQUAL_SLOT_PORTFOLIO` v1 definition owns finite-capital rules. `PortfolioSimulator` processes an explicit daily order—pre-open actions, open exits, session-start state, symbol-ordered candidates, entries, intraday exits, close marks—against one non-negative Decimal cash balance. Cash movements form an append-only typed ledger. Daily RAW-close marks plus mark-to-last warnings feed a separate analytics module for return, CAGR, sample volatility, effective-daily-risk-free Sharpe/Sortino, drawdown/Calmar, turnover, exposure, and position statistics.

The module is read-only with respect to PostgreSQL. It adds no schema, stored run, cache, snapshot, feature store, or queue. See [the Phase 6 portfolio/risk specification](portfolio_risk_engine.md).

## Phase 7 research composition boundary

Phase 7 adds a separate immutable `CONSENSUS_N_OF_M` v1 registry above the unchanged Phase 4 strategies. `ResearchService` canonicalizes strategy/version/parameter components, resolves their 13-feature union, and calls the authoritative Phase 4 batch technical path once for the entire historical universe. Each strategy consumes the same exact-date, availability-gated feature row; composition then preserves matched, not-matched, and potentially-outcome-changing insufficient-history states for every member.

Ephemeral evaluation remains read-only. Explicit save/replay endpoints write only immutable normalized experiment definitions and compact append-only run provenance to `research_experiments` and `research_experiment_runs`. Config, eligible dataset, and run fingerprints support order-invariant reproduction and distinguish eligible historical-data drift from engine/result drift. No market history, strategy rules, or executable expressions are persisted in experiment JSON. See [the Phase 7 research composition specification](research_composition.md).

## Phase 8 official/public NSE data flow

```text
official HTTPS or data/nse/inbox
             ↓
host/MIME/size/archive/schema validation
             ↓
typed MII / UDiFF / constituent / action / holiday parser
             ↓
checksum artifact + bounded structured issues + transactional write plan
             ↓
existing security / raw price / action / membership / calendar tables
             ↓
unchanged technical → scanner → strategy → backtest → portfolio → research paths
```

`daily_prices` remains the immutable RAW tape. Phase 8 does not calculate or store a second adjusted series; the existing availability-gated adjustment service remains authoritative. The MII master resolves symbol plus ISIN as one identity and refuses ambiguity. UDiFF imports batch-resolve all securities and existing date rows, accept only CM `EQ`, and never update conflicting history.

Current Nifty 200/500 CSVs must resolve exactly their expected member counts. A later snapshot closes previous open intervals on the day before the new snapshot while preserving earlier queries. It does not establish coverage before the first import. Corporate-action parsing promotes only unambiguous split/bonus rows with a real timezone-aware source publication timestamp; all missing timestamps are retained as issue evidence, not fabricated as `available_at`.

Direct requests are serialized and bounded. HTTP 403 ends immediately; timeouts, 429, and 5xx receive limited retries. The importer retains untrusted files only in project-local ignored checksum-addressed storage and never serves raw bulk artifacts through HTTP. See [source audit](nse_data_sources.md) and [operator runbook](data_ingestion.md).

## Phase 9 composition-aware historical flow

```text
inline Phase 7 request or saved experiment UUID
                    ↓
canonical Phase 7 composition + Phase 4 strategy resolution
                    ↓
historical PIT membership + calendar + batched price/action reads
                    ↓
one HistoricalTechnicalSeriesService union-feature computation
                    ↓
ordered per-session CONSENSUS_N_OF_M outcomes
                    ↓ MATCHED only
              canonical SetupEvents
             ↙                     ↘
Phase 5 TradeSimulator      Phase 6 PortfolioSimulator
```

`HistoricalCompositionService` owns source resolution, historical-range validation, point-in-time membership and data eligibility, shared component evaluation, and signal identity. It neither duplicates technical formulas nor calls the Phase 7 point endpoint once per date. Null component features retain the Phase 7 potentially-outcome-changing insufficient-history rule.

`COMPOSITION_NEXT_OPEN_FIXED_HOLD` v1 maps a matched close to the immediate next valid RAW open and the configured fixed-hold RAW open. It delegates execution, missing-price skips, overlap, stops/targets, corporate-action handling, India cash-delivery costs, analytics, portfolio allocation, the cash ledger, and risk metrics to the existing Phase 5 and Phase 6 engines. Both APIs consume the exact same ordered setup stream.

The source composition fingerprint, eligible dataset/signal fingerprint, and backtest/portfolio execution fingerprints are separate. Historical membership, calendar, price, action revision, source-artifact, ingestion-run, feature-version, and engine provenance are bound deterministically. Changing capital or holding assumptions does not relabel the underlying signal stream. Results remain synchronous and unpersisted; no schema change, cache, snapshot, feature store, queue, optimizer, or trading path was introduced. See [the Phase 9 composition backtesting specification](composition_backtesting.md).

## Phase 10 performance hardening

Phase 10 preserves the flow above and removes measured request-local duplication. Immutable strategy evaluation plans resolve feature dependencies, operators, and thresholds once; the range calculator skips unrequested family outputs while retaining the same authoritative formulas; and exact canonical strategy-result bytes avoid recursive generic object normalization in the inner loop. An explicit `PreparedHistoricalComposition` may feed both execution adapters in one internal workflow. Public endpoints remain independent, and compatibility validation prevents a context from being reused with a different source, range, adjustment policy, execution policy, or holding period.

This is not a global cache: the context is caller-owned, immutable at its record boundary, unpersisted, and contains one price/feature graph. Query batching remains 50 securities with 6/12/24 statements at 50/200/500 members. See [the Phase 10 performance report](phase10_performance.md) for profile evidence, exact parity coverage, memory considerations, and before/after measurements.

## Phase 11 point-in-time market-regime flow

```text
dedicated benchmark index prices (DEMO or OFFICIAL)
                    ↓ availability/source gate
authoritative SMA_50 / SMA_200 / MOM_3M_63D / VOLATILITY_20
                    ↓
prior-only 252-value nearest-rank volatility window
                    ↓
MARKET_REGIME_4_STATE v1 chronological timeline
             ↙                          ↘
transitions / duration / distribution    Phase 10 prepared composition
                                                   ↓
                                      unchanged Phase 5 trades
                                                   ↓
                                      signal-date regime attribution
```

`IndexDailyPrice` is a dedicated index-level entity; a benchmark is never stored as a fake equity. `(index_id, trading_date, source_mode)` is idempotent, source mode is explicit, and every row carries a point-in-time `available_at` plus optional Phase 8 artifact/run lineage. DEMO and OFFICIAL histories never silently substitute for each other.

The regime service calls the authoritative selected-feature calculator once over one ordered benchmark prefix. For each session, the current `VOLATILITY_20` is compared with the nearest-rank 80th percentile of at most 252 prior valid observations; the current observation enters the window only after classification. Fewer than 126 prior values or any missing trend input yields `INSUFFICIENT_HISTORY`. Classification precedence is high volatility, strict bull, strict bear, then sideways.

Regime attribution prepares the Phase 9/10 composition stream once and calls the existing Phase 5 simulator once. Every setup and trade is joined to the regime on `signal_date`, never entry date. The overlay cannot filter signals, alter execution, or change allocation. Trade counts and trade-level net P&L must reconcile before a response is returned. Existing Phase 5–10 fingerprints are untouched; timeline and attribution receive separate additive fingerprints. See [the Phase 11 market-regime specification](market_regime.md).

## Phase 12 official-data activation

Phase 12 adds no second ingestion architecture and no schema. The existing Phase 8 source-artifact pipeline now selects the official legacy CM bhavcopy before 2024-07-08 and UDiFF from that transition date onward. Bounded annual commands partition into independently committed 93-day windows, skip already-successful source identities, and may use complete official NIFTY 200 benchmark observations only to distinguish request dates (including special weekend sessions). Missing downloads never create guessed holidays.

Official NIFTY 200 historical OHLC enters the Phase 11 `index_daily_prices` table with `source_mode=OFFICIAL`, checksum-addressed artifact/run provenance, and truthful retrieval-time availability. It never substitutes for `NIFTYDEMO100`, and conflicts are recorded without overwriting existing rows. Current NIFTY membership imports retain their original Phase 8 semantics: a snapshot begins only on its explicit as-of date and is never projected backward.

`GET /api/v1/data-coverage` derives per-dataset `UNAVAILABLE`, `PARTIAL`, or `READY` state from persisted official rows. Security master, current NIFTY 200/500 membership, raw equity coverage, official benchmark history, corporate actions, and calendar evidence are reported separately so a partially activated installation cannot imply complete historical point-in-time coverage. See [the Phase 12 activation report](official_market_data.md).
