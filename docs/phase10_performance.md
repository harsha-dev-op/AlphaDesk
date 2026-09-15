# AlphaDesk Phase 10 historical-research performance hardening

## Scope and invariants

Phase 10 optimizes the existing Phase 9 composition-aware historical path. It adds no product feature, strategy, indicator, database object, cache, worker, ranking, recommendation, or trading capability. The two public research endpoints remain independent and synchronous. `ALPHADESK_CORE_TECHNICAL` v1 remains 36 features and `ALPHADESK_STRATEGY_TECHNICAL` v1 remains 38 features.

The authoritative Phase 2 formulas, warm-ups, Decimal behavior, Phase 4 conditions, Phase 7 consensus rule, point-in-time eligibility, Phase 5/6 execution and accounting, costs, ordering, and fingerprint definitions are unchanged. A deterministic Phase 9 golden fixture freezes dataset, historical signal, component outcome, setup, backtest, trade, cost, portfolio, ledger, equity, and analytics results.

## Profile evidence

The before profile used `cProfile`, `pstats`, existing phase timers, and SQLAlchemy statement counters with the guarded local PostgreSQL synthetic workload. For a 50-security, 300-session run of both endpoints, 21.9 million calls took 33.28 seconds under profiling. The leading cumulative consumers were:

- Historical preparation: 32.51 s across two independent endpoint calls.
- Component result fingerprints: 9.86 s for 90,000 results, including 5.90 s of recursive canonicalization.
- Phase 4 condition evaluation: 6.99 s, including 546,976 Pydantic model constructions/validations in the profiled call graph.
- Feature batch computation: 7.01 s, with 5.92 s in per-security calculation and 4.22 s in the authoritative feature frame.
- SQL repository loading: about 1.00 s; query growth was already bounded and was not the principal bottleneck.

The repeatable workflow is:

```powershell
cd backend
.\.venv\Scripts\python -m app.benchmarks.postgres_composition_research --profile-count 50
```

It refuses non-local/non-`alphadesk_dev` database identities, uses the reserved synthetic namespace, prints cumulative `pstats`, and cleans in `finally`. It creates no `.prof` artifact.

## Optimizations

1. Each request resolves immutable `PreparedStrategyEvaluation` plans once. Historical rows use the same authoritative operator function but do not allocate response-only `StrategyConditionResult` models.
2. Strategy-result hashing emits the exact pre-existing canonical JSON bytes directly from validated Decimal/boolean/null feature scalars. Generic Phase 4 hashing remains available, and parameterized tests compare both paths byte for byte.
3. The existing range calculator still owns all formulas. When a composition requests a feature union, family calculators now skip unrelated return, SMA/EMA/distance, liquidity, session, and price-structure outputs. Full-set calculation is unchanged and tested against all 38 registered features.
4. `run_backtest_prepared` and `run_portfolio_prepared` accept one explicit request-scoped `PreparedHistoricalComposition`. Compatibility checks bind the range, adjustment/execution inputs, holding period, and composition source. Public endpoint calls still prepare independently; there is no hidden or cross-request cache.
5. The benchmark measures the two independent endpoints first, then prepares once and feeds both adapters. The shared adapters issue zero SQL statements and reproduce the independent run fingerprints.

Internal outcomes and observations remain frozen/slotted records, previews remain bounded, and the 500×300 context retains one feature-series graph plus compact outcome/setup records. The optimization does not duplicate price or feature timelines and does not persist prepared data.

## Reproducible benchmark

Both measurements used the same Windows host, local PostgreSQL 17.11 database `alphadesk_dev` as `alphadesk`, 300 sessions, deterministic RAW synthetic OHLCV, all three Phase 4 strategies, `CONSENSUS_N_OF_M` v1 with N=2, the same versioned execution/cost defaults, and the same 50-row repository batch size. Timings are wall-clock and naturally contain local-host noise. "Prep stages" is the same measured sum of repository fetch, feature generation, and composition evaluation in the independent backtest run; it intentionally excludes execution, analytics, response construction, and serialization.

| Members | Metric | Before | After | Speedup | Reduction |
| ---: | --- | ---: | ---: | ---: | ---: |
| 50 | Prep stages | 8,698.978 ms | 4,713.054 ms | 1.846× | 45.8% |
| 50 | Backtest total | 9,033.495 ms | 5,013.959 ms | 1.802× | 44.5% |
| 50 | Portfolio total | 9,931.691 ms | 5,498.653 ms | 1.806× | 44.6% |
| 200 | Prep stages | 35,175.861 ms | 19,733.002 ms | 1.783× | 43.9% |
| 200 | Backtest total | 36,316.975 ms | 21,416.627 ms | 1.696× | 41.0% |
| 200 | Portfolio total | 32,436.420 ms | 21,502.038 ms | 1.509× | 33.7% |
| 500 | Prep stages | 95,003.275 ms | 45,871.002 ms | 2.071× | 51.7% |
| 500 | Backtest total | 98,263.458 ms | 48,498.778 ms | 2.026× | 50.6% |
| 500 | Portfolio total | 95,814.584 ms | 49,069.114 ms | 1.953× | 48.8% |

The optimized explicit shared preparations measured 4,706.803 ms, 19,277.582 ms, and 46,621.602 ms for 50/200/500 members. Their backtest adapters measured 141.939/642.765/2,022.356 ms and portfolio adapters 1,065.151/2,295.462/4,277.385 ms, with zero SQL statements in every adapter.

| Members | Price rows | Evaluations | Setups | Backtest trades | Portfolio positions | SELECTs/statements per independent endpoint |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 50 | 15,000 | 15,000 | 5,050 | 250 | 50 | 6 / 6 |
| 200 | 60,000 | 60,000 | 20,200 | 1,000 | 50 | 12 / 12 |
| 500 | 150,000 | 150,000 | 50,500 | 2,500 | 50 | 24 / 24 |

Serialization stayed below 5 ms for the bounded responses. Setup/trade/position counts and independent/shared fingerprints were identical. Cleanup removed all 500 securities, 150,000 prices, three indices, 750 memberships, and 300 calendar rows; every reserved-namespace count was zero afterward.

Run the exact benchmark with:

```powershell
cd backend
.\.venv\Scripts\python -m app.benchmarks.postgres_composition_research
```

Use `--cleanup-only` for guarded reserved-namespace cleanup.

## Correctness and deferred work

Tests cover full 38-feature range/point parity, selected-union/full-frame parity across warm-up, constant, tiny Decimal, large-price, zero/low-volume, gapped, and large-range inputs, generic/fast condition and fingerprint parity, golden Phase 9 output identities, complete shared/independent response parity, RAW/ADJUSTED action behavior, future price/membership/action-revision invariance, inline/saved sources, ordering, persistence, and bounded queries.

Remaining time is primarily canonical per-observation outcome construction/hashing, exact Decimal rolling calculations, SQLAlchemy row materialization, and the intentionally separate Phase 5/6 simulations. Possible future work includes rigorously proven incremental canonical hashing or lower-overhead projected row decoding. Rolling-sum/deque/vectorized replacements were deliberately deferred because Decimal operation order, warm-up, Wilder initialization, nulls, and fingerprint parity are more important than speculative speed. No process-global cache, Redis, feature store, schema change, or materialized timeline is justified by this phase.
