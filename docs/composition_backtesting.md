# AlphaDesk Phase 9 composition-aware historical research

## Purpose and boundary

Phase 9 replays an exact Phase 7 `CONSENSUS_N_OF_M` composition across a historical range, converts only matched composition outcomes into one canonical setup-event stream, and passes that same stream to the existing Phase 5 independent-trade and Phase 6 shared-capital simulators. It is synchronous, read-only research. It does not rank securities, optimize parameters, predict returns, recommend trades, connect to a broker, or place orders.

```text
inline Phase 7 request or immutable saved experiment
                         ↓
canonical composition definition + authoritative Phase 4 strategies
                         ↓
historical membership + one union feature-series computation
                         ↓
per-session component decisions → CONSENSUS_N_OF_M outcomes
                         ↓ MATCHED only
                 canonical SetupEvent stream
                    ↙                   ↘
       Phase 5 TradeSimulator     Phase 6 PortfolioSimulator
```

No migration, cache, feature store, snapshot, queue, or persisted Phase 9 run was added.

## Source and configuration separation

Every request supplies exactly one source:

- An inline, fully typed Phase 7 `CompositionEvaluationRequest`.
- A saved experiment UUID whose immutable normalized request and configuration fingerprint are verified before use.

The source retains its point-evaluation universe, observation date, as-of timestamp, and adjustment mode as immutable provenance. Phase 9's top-level universe, start/end dates, adjustment mode, execution assumptions, and portfolio assumptions separately define the historical run. This prevents a saved point evaluation from being silently rewritten while still allowing it to drive an explicitly configured range replay.

Components are normalized with the Phase 7 resolver and every strategy/version/parameter is resolved through the authoritative Phase 4 registry. Component order and equivalent Decimal representations cannot change the composition configuration fingerprint. The 38-feature strategy set remains unchanged; no `EMA_200`, alias, scanner-specific indicator, or Phase 9 indicator exists.

## Historical evaluation and missing data

For every trading session and historically eligible member, the service evaluates all components against a shared exact-session feature row. It preserves the Phase 7 threshold rule:

- `MATCHED` when `matched_count >= N`.
- `INSUFFICIENT_FEATURE_HISTORY` when the threshold is not met but `matched_count + insufficient_count >= N`.
- `NOT_MATCHED` otherwise.

Null feature values remain insufficient and are never converted to zero or false. Every decision is ordered by session date, symbol, and canonical component order. The response returns bounded outcome and trade previews while fingerprints bind the complete ordered evaluation, not just the preview.

## Point-in-time safeguards

- Historical index membership is resolved over inclusive validity intervals and official-snapshot coverage is enforced.
- Price history is capped at each decision session; an exact row must be available by that exchange close.
- Corporate actions are eligible only when `available_at` is no later than the decision clock, and only the latest eligible revision is used.
- Later prices, memberships, action dates, and later-available action revisions cannot change an earlier signal fingerprint.
- RAW and ADJUSTED feature inputs are explicit. Execution, exits, and portfolio marks continue to use RAW market prices.
- Price, corporate-action, membership, calendar, feature-version, source-artifact, and ingestion-run lineage contribute to the dataset fingerprint.

The service loads the member set, calendar, projected price histories, and eligible action histories in bounded batches. It never calls a point-evaluation API per security or session.

## Execution policy and engine reuse

`COMPOSITION_NEXT_OPEN_FIXED_HOLD` version 1 is a frozen execution-policy definition. A matched close decision becomes eligible at the immediate next valid RAW open and exits at the configured fixed-hold RAW open. Holding sessions default to 20 and are restricted to 1–252. Missing immediate entry prices and overlap conflicts retain the Phase 5 skip semantics; the policy remains long-only and permits at most one open trade per security.

The backtest endpoint feeds the canonical setups directly to the existing `TradeSimulator`, India cash-delivery cost model, and trade analytics. The portfolio endpoint feeds the same setups to the existing `PortfolioSimulator`, allocation policy, cash ledger, cost model, marks, and risk analytics. Stop/target behavior, same-bar ambiguity, action handling, affordability, integer shares, daily event order, and force-close behavior are not reimplemented.

## Fingerprints

Canonical JSON uses sorted mappings, ordered dates/symbols/components, UTC-normalized timestamps, ISO dates, and normalized Decimal strings.

- `composition_config_fingerprint` identifies the exact normalized Phase 7 source.
- `dataset_fingerprint` identifies eligible membership, calendar, feature/version, price, action, artifact, and ingestion lineage for the range.
- `signal_fingerprint` identifies source configuration, historical range, adjustment mode, engine provenance, dataset, and every ordered composition outcome.
- `backtest_run_fingerprint` adds Phase 5 execution, costs, stop/target, capital, and holding configuration to the signal identity.
- `portfolio_run_fingerprint` adds Phase 6 portfolio, allocation, capital, execution, and cost configuration to the same signal identity.

Changing holding sessions or capital changes the relevant execution fingerprint without changing the signal fingerprint. Inline and saved sources with the exact same normalized composition produce the same signal identity.

## API

| Method | Route | Behavior |
| --- | --- | --- |
| POST | `/api/v1/research/backtest` | Composition range replay plus existing Phase 5 independent-trade simulation |
| POST | `/api/v1/research/portfolio` | The same composition setup stream plus existing Phase 6 shared-capital simulation |

Requests reject ambiguous/missing sources, unknown saved experiments, unsupported policy/strategy/execution versions, invalid ranges, invalid holding periods, invalid portfolio inputs, and incomplete official historical membership. Domain validation is sanitized; stack traces and SQL details are not returned.

## Frontend

The `/research` Historical Analysis view supports inline evaluated compositions and immutable saved experiments. It exposes explicit range, universe, RAW/ADJUSTED, holding, slippage, stop/target, and applicable capital/allocation inputs. Backtest results include headline metrics, bounded trades, provenance, and fingerprints. Portfolio results include risk metrics, equity/drawdown charts, positions, trades, warnings, and the shared signal identity. The UI states that outputs are historical research simulations, not recommendations or live execution.

## PostgreSQL benchmark

The guarded benchmark uses only local PostgreSQL database `alphadesk_dev` as user `alphadesk`, a separate synthetic exchange/date/symbol/index namespace, 300 sessions per security, all three Phase 4 strategies, `CONSENSUS_N_OF_M` v1 with N=2, and ADJUSTED features. It measures each endpoint separately and removes the namespace in `finally`.

| Members | Price rows | Endpoint | Wall time | SELECTs | Fetch | Features | Composition | Phase 5/6 execution |
| ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 50 | 15,000 | Backtest | 8,968.696 ms | 6 | 305.059 ms | 1,752.862 ms | 6,570.657 ms | 116.319 ms |
| 50 | 15,000 | Portfolio | 9,101.892 ms | 6 | 427.425 ms | 1,565.203 ms | 6,519.244 ms | 397.780 ms |
| 200 | 60,000 | Backtest | 36,732.667 ms | 12 | 1,547.482 ms | 7,575.982 ms | 26,296.946 ms | 568.412 ms |
| 200 | 60,000 | Portfolio | 38,623.089 ms | 12 | 1,448.966 ms | 7,030.166 ms | 27,734.494 ms | 1,616.347 ms |
| 500 | 150,000 | Backtest | 94,931.425 ms | 24 | 3,109.055 ms | 18,792.438 ms | 71,973.298 ms | 396.892 ms |
| 500 | 150,000 | Portfolio | 70,771.067 ms | 24 | 1,221.313 ms | 5,805.360 ms | 57,294.596 ms | 4,232.617 ms |

The query count grows with 50-security chunks, not with sessions or strategy count. Serialization remained below 6 ms for bounded responses. Both endpoints produced the same signal fingerprint in every case. Cleanup removed 500 securities, 150,000 prices, three indices, 750 membership rows, and 300 calendar rows; post-cleanup counts were all zero.

Run the guarded measurement with:

```powershell
cd backend
.\.venv\Scripts\python -m app.benchmarks.postgres_composition_research
```

Use `--cleanup-only` to remove the reserved namespace without running measurements.

## Verification and limitations

Phase 9 tests cover range evaluation, exact Phase 7 decision parity, all registered strategy features, missing-history behavior, deterministic/order-invariant fingerprints, inline/saved parity and read-only behavior, configuration separation, future-data invariance, RAW/ADJUSTED provenance, Phase 5 costs/exits/actions, Phase 6 cash/capacity/event ordering, bounded query growth, validation, official-membership guards, and the absence of `EMA_200`.

This remains EOD, long-only, synchronous historical research. It has no historical-market snapshotting, distributed execution, ranking, optimization, ML, shorting, leverage, derivatives, intraday engine, broker integration, recommendation language, or paper/live order path. Those concerns are intentionally outside Phase 9.
