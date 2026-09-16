# AlphaDesk Phase 11 market-regime research

## Purpose and boundary

`MARKET_REGIME_4_STATE` v1 is a deterministic, explainable, point-in-time research classifier for a benchmark index. It is not a predictive market model. It does not forecast transitions, rank strategies, filter signals, change Phase 5 execution, alter Phase 6 sizing, or provide trading advice.

Benchmark levels are stored in the dedicated `index_daily_prices` table. AlphaDesk never models an index as a fake equity. Every row identifies its index, date, OHLC level, `DEMO` or `OFFICIAL` source mode, provider source, knowledge timestamp, and optional source-artifact/ingestion-run provenance. The unique `(index_id, trading_date, source_mode)` identity makes DEMO seeding idempotent while allowing an OFFICIAL source to remain separate.

NIFTY 200 is the preferred benchmark identifier when represented. Phase 11 does not fetch or fabricate official history. A known benchmark with no rows for the requested source returns an explicit `UNAVAILABLE` response. The deterministic `NIFTYDEMO100` history is fictional, visibly labeled DEMO, and exists only for development and tests.

## Exact v1 classification

The engine reuses the existing authoritative `SMA_50`, `SMA_200`, `MOM_3M_63D`, and `VOLATILITY_20` calculations. The 36-feature Core v1 and 38-feature Strategy v1 registries are unchanged; no new indicator or `EMA_200` exists.

For date D, build the volatility reference from valid `VOLATILITY_20` observations strictly before D. Keep at most the previous 252 valid observations and require at least 126. Sort the bounded window ascending and select the one-based nearest rank:

```text
rank = ceil(0.80 × N)
threshold = ordered_prior_values[rank - 1]
```

The current session enters the rolling window only after its classification. Boundary precedence is exact:

1. Missing `SMA_50`, `SMA_200`, `MOM_3M_63D`, or `VOLATILITY_20`, or fewer than 126 prior valid volatility values: `INSUFFICIENT_HISTORY`.
2. `VOLATILITY_20 >= prior_threshold`: `HIGH_VOLATILITY`.
3. `close > SMA_200` and `SMA_50 > SMA_200` and `MOM_3M_63D > 0`: `TRENDING_BULL`.
4. `close < SMA_200` and `SMA_50 < SMA_200` and `MOM_3M_63D < 0`: `TRENDING_BEAR`.
5. Otherwise: `SIDEWAYS`.

Equality triggers high volatility, but equality at either moving-average boundary or zero momentum does not satisfy bull or bear.

## Point-in-time and timeline behavior

History queries require one explicit source mode and use only index rows whose `available_at` is no later than the request `as_of` (or current knowledge time when omitted). They cap output at the requested end date. Appending future prices or a future volatility spike cannot change earlier calculations because feature formulas and the percentile window are chronological and prior-only.

The response exposes requested dates, benchmark/source identity, complete stored coverage, the first classifiable date, chronological session diagnostics, four-regime counts and percentages, insufficient-history count, latest available classification, its consecutive-session start/duration, and transitions. A transition occurs on the first session whose classification differs from the immediately preceding returned session. Transitions involving insufficient history remain explicit but do not enter the four-regime percentage denominator.

The timeline fingerprint binds benchmark identity, definition/version and parameter values, requested range/as-of, source mode, eligible benchmark dataset/provenance, and ordered dates, classifications, and required metrics. Future rows beyond the request horizon do not change it.

## Research attribution

Attribution composes the existing Phase 9/10 and Phase 5 paths:

```text
PreparedHistoricalComposition → canonical SetupEvents → existing TradeSimulator
                                              ↓
                            benchmark regime on setup.signal_date
                                              ↓
                              deterministic performance buckets
```

The join date is the setup/signal date, not the next-open entry date. Each signal and every executed trade enters exactly one of `TRENDING_BULL`, `TRENDING_BEAR`, `SIDEWAYS`, `HIGH_VOLATILITY`, or `INSUFFICIENT_HISTORY`. Missing benchmark sessions are retained in the insufficient-history bucket.

Each bucket reports signal, executed, and skipped counts; winners, losers, breakeven trades, win rate, summed positive/negative net trade P&L, total net P&L, mean/median net return, average holding sessions, profit factor, expectancy, and average win/loss. These values reuse authoritative Phase 5 `SimulatedTrade` results and analytics helpers. AlphaDesk refuses a response unless bucket trade counts and net P&L reconcile exactly with the backtest.

The additive attribution fingerprint binds the historical signal fingerprint, unchanged backtest run fingerprint, regime timeline fingerprint, ordered signal-date mapping, and bucket output. Existing signal, backtest, portfolio, trade, and composition fingerprints remain byte-for-byte unchanged.

## API and UI

- `GET /api/v1/regimes/metadata` returns definitions, rules, benchmarks, exact source coverage, defaults, and source modes.
- `POST /api/v1/regimes/history` returns an available or truthful unavailable timeline.
- `POST /api/v1/regimes/research-attribution` composes the existing historical backtest request rather than defining another strategy language.

The `/regimes` workspace shows “Latest available regime,” not an unsupported live-market claim. It always shows benchmark, source badge, as-of date, coverage, duration, diagnostics, timeline, distribution, transitions, and attribution reconciliation. DEMO is visually explicit.

## Performance and query behavior

The implementation builds one benchmark feature frame, maintains the 252-observation percentile window with a deque plus sorted bounded list, and performs no per-date database query. The guarded local PostgreSQL 17.11 benchmark produced:

| Sessions | Wall time | Repository | Features | Classification | SELECTs/statements |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 300 | 27.290 ms | 9.494 ms | 5.611 ms | 8.536 ms | 2 / 2 |
| 1,000 | 78.983 ms | 25.489 ms | 23.302 ms | 28.266 ms | 2 / 2 |
| 3,000 | 240.911 ms | 74.037 ms | 77.335 ms | 87.120 ms | 2 / 2 |

Run it with `python -m app.benchmarks.postgres_regimes`. It refuses non-local or unexpected database identities and cleans its reserved synthetic namespace in `finally`.

## Limitations and deferred work

- Phase 11 ships no real official benchmark backfill. OFFICIAL coverage is truthful but may be empty.
- The v1 classifier is fixed and descriptive; there is no ML, HMM, clustering, forecasting, transition probability, strategy selection, or regime-dependent trading behavior.
- Results are computed synchronously and are not persisted. No Redis, Celery, feature store, materialized regime timeline, or distributed worker is justified.
- Phase 12+ may add a separately reviewed responsible official benchmark-history ingestion source, but it must preserve source timestamps, provenance, and all point-in-time invariants.
