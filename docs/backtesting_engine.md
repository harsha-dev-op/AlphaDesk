# AlphaDesk Phase 5 point-in-time backtesting engine

## Purpose and boundary

Phase 5 replays the immutable Phase 4 research strategies over historical index membership and then measures what happened under an explicit, versioned execution policy. It is an offline research simulator. It does not recommend securities, allocate a portfolio, optimize strategy parameters, connect to a broker, paper trade, or place orders.

The primary analytical unit is an independent fixed-notional simulated trade. Overlapping trades do not compete for capital. Consequently AlphaDesk does not report a portfolio equity curve, CAGR, Sharpe, Sortino, portfolio drawdown, leverage, or exposure in Phase 5.

## Architecture

```text
Backtest API → BacktestService → historical membership intervals + trading calendar
                              → projected OHLCV/action revision batches
                              → HistoricalTechnicalSeriesService
                              → authoritative Phase 2 calculators
                              → immutable Phase 4 strategy rules
                              → SetupEvent stream
                              → versioned TradeSimulator profile
                              → versioned India cash-delivery costs
                              → closed/unclosed simulated outcomes
                              → trade analytics, OOS segments, fingerprints, preview
```

Backtests are calculated on demand. No `backtest_runs`, simulated-order, simulated-position, cache, snapshot, or feature-store table is added, and Phase 5 requires no Alembic migration.

## Strategy and backtest-profile separation

The Phase 4 Strategy Registry remains the authoritative source for setup conditions. Phase 5 does not change a strategy rule, threshold, feature version, direction, or EOD timing. Execution assumptions live in a separate frozen code registry.

`NEXT_OPEN_FIXED_HOLD` version `1` is compatible with the three Phase 4 strategies. Its baseline holding horizons are 20 sessions for Momentum Trend, 20 for 20-Day Breakout, and 10 for Mean-Reversion Pullback. These are transparent research horizons, not optimized values. A request may override the holding period; the override participates in the config and run fingerprints.

Material changes to entry, exit, quantity, overlap, missing-price, forced-end, or cost semantics require a new profile or cost-model version. Existing version behavior must not be silently changed.

## Historical feature computation

`HistoricalTechnicalSeriesService.compute_batch` loads each security's ordered projected price history and eligible action revision history once per bounded 50-security chunk. It computes causal historical rows with the existing Decimal calculator primitives. For ADJUSTED features, it caches one authoritative frame per distinct point-in-time corporate-action knowledge regime; later revisions can affect later decisions but cannot rewrite earlier emitted observations.

Backtests request only the selected strategy's registered dependencies. The calculator orchestrator skips unrelated feature families and unused annualized-volatility windows, but the formulas for every requested feature are unchanged. The default full-frame path still calculates the complete registered set. Tests compare selected rows to the full authoritative frame and compare historical series observations to independent `TechnicalFeatureService.compute` results in RAW and ADJUSTED modes.

The Core v1 set remains 36 features. The Strategy v1 set remains those 36 plus only `PRIOR_HIGH_20` and `BREAKOUT_PCT_20`. `PRIOR_HIGH_20` still uses `max(high[t-20:t])` and excludes the current row. `EMA_200` does not exist.

## Decision data versus outcome data

For each historical trading session D, the decision timestamp is that exchange calendar session's close in Asia/Kolkata. Setup conditions may use the exact D EOD row only after that timestamp. Membership is resolved from the inclusive historical interval valid on D. A security that later leaves the index may finish an already-open simulated trade; index departure is not an exit rule.

Signal features and the signal fingerprint contain only the price prefix and corporate-action revision state eligible at D. D+1 opens and subsequent highs/lows may change entry or outcome, but not whether the D setup matched or its signal fingerprint. Action revisions are filtered by `available_at`; a revision that becomes knowable later cannot leak into an earlier signal.

Outcome data is intentionally read after a valid historical decision to simulate the subsequent path. It never participates in the original condition evaluation.

## Entry, quantity, holding period, and overlap

- Signal: D after the registered EOD close.
- Entry: RAW open of the immediate next valid market session E. Same-day close entry is prohibited.
- Long entry slippage: `raw_open × (1 + slippage_bps / 10000)`, rounded to the price quantum. Default slippage is 5 bps and is configurable within request bounds.
- Missing entry: if E has no positive RAW open, the setup is skipped as `ENTRY_PRICE_UNAVAILABLE`. No later favorable entry search occurs.
- Out-of-range entry: a signal whose next session is outside the requested range is skipped as `ENTRY_OUTSIDE_TEST_RANGE`.
- Quantity: `floor(trade_notional / slipped_entry_price)`. Initial quantity is an integer; the default independent notional is ₹100,000. A quantity below one is skipped as `INSUFFICIENT_NOTIONAL_FOR_ONE_SHARE`.
- Overlap: `ONE_OPEN_TRADE_PER_SECURITY`. A later setup while the prior simulated trade remains open is recorded as `ALREADY_OPEN_POSITION`; re-entry is allowed after the earlier exit.

If `holding_sessions = N`, E is holding session 1. Stops and targets can operate from E onward. With no earlier exit, the scheduled time exit is the RAW open of the session immediately after N completed holding sessions. Thus a Monday-close signal enters Tuesday open and, for N=1, exits Wednesday open.

## Stops, targets, daily ambiguity, and exits

Stops and profit targets default to null/disabled. When enabled, long levels start at slipped entry price times `1 - stop_loss_pct` or `1 + profit_target_pct`. RAW daily OHLC determines whether a level was reached.

For a long stop, an open at or below the stop uses that open as the base fill; otherwise a low at or below the stop uses the stop level. For a target, an open at or above the target uses that open; otherwise a high at or above the target uses the target level. When both thresholds are touched and the open does not establish an order, AlphaDesk applies the conservative `STOP_FIRST` rule and emits `AMBIGUOUS_INTRADAY_PATH_STOP_FIRST`.

All long exits apply adverse slippage: `base_exit_price × (1 - slippage_bps / 10000)`. Typed exit reasons are `TIME_EXIT`, `STOP_LOSS`, `PROFIT_TARGET`, `FORCED_END_OF_TEST`, and `EXIT_PRICE_UNAVAILABLE`.

If the scheduled exit open is unavailable, the simulator checks at most the configured number of subsequent valid market sessions; the default is five. If no positive open appears, the outcome remains explicitly unclosed with `EXIT_PRICE_UNAVAILABLE`, null realized analytics, and a warning. If the normal holding horizon runs beyond the requested end, `FORCE_CLOSE_LAST_AVAILABLE_CLOSE` uses the final available RAW close inside the requested range, applies exit slippage, and marks `FORCED_END_OF_TEST`.

MAE and MFE use only the post-entry path. A time exit includes the exit open. A gap exit stops the path at the open. Intraday threshold exits stop at the deterministic threshold event. Corporate-action quantity changes normalize high/low economic exposure back to the entry-share basis.

## Corporate actions while a trade is open

Feature generation retains the Phase 3.6 `available_at` and append-only revision rules. Trade accounting resolves the action state knowable by the effective session open. Supported `STOCK_SPLIT` and `BONUS` events reuse `PriceAdjustmentService.factor_for`:

- share quantity is divided by the price adjustment factor;
- per-share stop and target references are multiplied by the factor;
- exit value uses the resulting quantity and RAW price.

A 2-for-1 split and a 1:1 bonus therefore double quantity and halve per-share references, preserving economic value. Fractional post-action entitlements are retained as Decimal research quantities when a ratio does not map the integer entry quantity exactly; broker cash-in-lieu handling is not modeled. Unsupported action types within an open path emit `UNSUPPORTED_CORPORATE_ACTION:<TYPE>` instead of silently claiming they were modeled. Dividends, rights, mergers, demergers, and contract-note-specific treatment remain out of scope.

## India NSE cash-delivery cost model

`INDIA_NSE_CASH_DELIVERY_2026_09` version `1` freezes these research assumptions:

| Component | Effective rule |
| --- | --- |
| STT | 0.1% (`0.001`) on buy turnover and 0.1% on sell turnover |
| NSE cash transaction charge | 0.00307% (`0.0000307`) on both legs |
| SEBI turnover charge | ₹10/crore, decimal `0.000001`, on both legs |
| GST | 18% of brokerage + exchange transaction charge + SEBI charge |
| Stamp duty | 0.015% (`0.00015`) on the buy leg only |
| Brokerage | configurable flat per order plus configurable turnover rate; defaults to zero |
| DP charge | configurable once on the sell leg; defaults to zero |

All arithmetic uses Decimal. Monetary components are rounded to paise with half-up rounding. STT is rounded to the nearest rupee, half up, independently per leg, then represented to paise. GST uses the displayed paise-rounded brokerage, exchange, and SEBI components as its taxable base. Total charges are the sum of rounded components. These are deterministic research calculations, not a claim of penny-perfect broker contract-note reconciliation; aggregation and broker rules can differ.

The cost fingerprint includes code/version, every statutory rate, GST base, rounding semantics, brokerage flat/rate overrides, DP override, and their application semantics. Changing any effective cost parameter changes the backtest config/run fingerprint.

## Trade records and analytics

Each simulated outcome contains stable security and strategy identity, signal/entry/exit clocks, raw and slipped prices, entry and current quantity, notional, both leg cost breakdowns, optional levels, typed exit reason, holding sessions, gross/net P&L and return, MAE/MFE, applicable action effects, warnings, and deterministic strategy/signal/trade fingerprints.

Setup count, executable setup count, executed trade count, skip reasons, normally closed count, forced-end count, and unclosed data-quality outcomes remain distinct. Summary statistics use the complete trade set even when API trade details are truncated.

Closed trade analytics include win/loss/breakeven counts and rates; average/median gross and net returns; average/total gross and net P&L; costs; average cost; profit factor; expectancy; best/worst; population standard deviation of net returns; linearly interpolated 5th/25th/50th/75th/95th percentiles; holding periods; and average MAE/MFE.

`profit_factor = sum(positive net P&L) / abs(sum(negative net P&L))`. It is null with a warning when no losing denominator exists. `expectancy_per_trade = total net P&L / closed trade count`. `cost_drag_pct = total modeled costs / total deployed notional`. Undefined statistics return null plus a transparent warning. Samples below 10 trades receive `VERY_LOW_SAMPLE_SIZE`; samples below 30 receive `LOW_SAMPLE_SIZE`; zero closed trades receive `NO_TRADES`.

Yearly rows group trades and setups by signal year. An optional `out_of_sample_start_date` classifies completed trades by signal date into `IN_SAMPLE` and `OUT_OF_SAMPLE`; both use the exact same fixed strategy parameters. No training or optimization occurs. Rolling-window output was deliberately deferred because the explicit holdout is the smaller stable Phase 5 scope.

## Fingerprints

- Config fingerprint: strategy/version/effective parameters and strategy fingerprint; universe/range; RAW/ADJUSTED policy; profile/version and policies; holding, notional, slippage, stops/target, exit delay; cost model/rates/overrides; and OOS boundary.
- Dataset fingerprint: historical membership intervals, exchange sessions, projected price prefixes/outcomes, relevant action revision chains, feature-set/version data, and security-series dataset fingerprints.
- Run fingerprint: config fingerprint, dataset fingerprint, ordered setup fingerprints, ordered trade fingerprints, and deterministic skip reasons.

Wall-clock execution timestamps and measured timings are excluded. `trade_detail_limit` affects only response preview and is intentionally excluded from research config/run identity.

## APIs and response safety

`GET /api/v1/backtests/metadata` exposes profiles, compatible strategies, default holding periods, parameter bounds, exact cost rates and rounding, universes, supported policies, warning thresholds, and hard limits.

`POST /api/v1/backtests/run` validates the versioned strategy/profile/cost identifiers, strategy parameters, dates, price policy, execution overrides, broker costs, OOS boundary, and preview limit. The response includes normalized inputs, metadata, fingerprints, setup/skip counts, complete analytics, aggregate costs, yearly/OOS rows, warnings, deterministic trade preview, and stage timings.

The default trade preview is 500 and the maximum is 5,000. `total_trade_count`, `returned_trade_count`, and `trades_truncated` make truncation explicit. Setups are generated in signal-date/symbol order; trades are returned in entry-date/symbol/trade-ID order. No profitability ranking occurs.

## Frontend

`/backtests` is an unlocked, read-only institutional-terminal workspace. Controls come from backend metadata where possible and cover strategy/version/parameters, historical universe/range, feature policy, profile/version, holding sessions, notional, slippage, optional stop/target, bounded exit delay, cost model, brokerage/DP overrides, and optional OOS boundary.

The UI distinguishes matched setups from simulated trades, shows full-run analytics and cost totals, expands trade fingerprints/costs/actions/warnings, provides yearly and OOS tables, and displays a trade-return histogram. When the trade preview is truncated, the histogram is labeled as preview-only. It is never called a portfolio equity curve.

## PostgreSQL benchmark

The guarded local PostgreSQL 17.11 benchmark reused the deterministic Phase 3.6 `XADB` namespace with 300 sessions per security. Warm medians exclude the first of three direct runs; the API sample follows warmed runs.

| Members | Price rows | Queries | Warm service + serialization | API sample | Target | Result |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 50 | 15,000 | 6 | 669.832 ms | 1,896.147 ms | ≤5 s | pass |
| 200 | 60,000 | 12 | 4,144.842 ms | 3,018.233 ms | ≤15 s | pass |
| 500 | 150,000 | 24 | 8,397.552 ms | 19,172.808 ms | ≤30 s | pass |

The query pattern is `4 + 2 × ceil(member_count / 50)`: index, overlapping memberships, calendar range, ingestion provenance, then projected prices and eligible action-revision rows per bounded chunk. It does not grow by session count and has no per-date or per-security query. Feature-series calculation remains the primary runtime cost. A profile showed unused annualized-volatility calculations dominated the representative Breakout workload; the only optimization was to skip unrequested registered feature families/windows while retaining the same authoritative formulas. Equivalent pre/post run fingerprints were observed. The benchmark namespace was removed afterward and all seven namespace counts returned to zero. Approximate peak memory was not reported because a stable low-overhead Windows process metric was unavailable.

## Known limitations and future handoff

- Daily OHLC cannot reconstruct intraday path; `STOP_FIRST` is deliberately conservative.
- Execution assumes the recorded RAW open/threshold/close plus deterministic slippage, not order-book liquidity or market impact.
- Contract-note aggregation, broker caps/minimums, DP nuances, fractional corporate-action cash settlement, and unsupported actions are not modeled.
- An exit still unavailable after the bounded search remains explicitly unclosed and is excluded from realized-return statistics.
- Results are independent fixed-notional events, not portfolio accounting.
- Rolling windows, saved experiments, concurrent-load testing, persistence, capital allocation, portfolio risk, shorting, leverage, derivatives, intraday behavior, optimization, ML, brokers, paper trading, and live trading are not implemented.

The originally recommended next boundary was a separate versioned portfolio/risk simulation layer consuming Phase 5's authoritative setup and execution inputs without mutating Phase 4 strategies or Phase 5 profiles. Phase 6 implements that boundary as described below.

## Phase 6 consumer

Phase 6 now implements that boundary as a separate service and immutable portfolio-policy registry. A small internal extraction, `BacktestService.prepare_research`, exposes the exact historical setup stream and loaded point-in-time series to both consumers. Phase 5 still simulates independent fixed-notional trades and returns the same controlled config/dataset/run/trade fingerprints; it does not silently acquire capital constraints or portfolio statistics.

The portfolio consumer reuses public Phase 5 slippage, intraday-exit, split/bonus, cost calculation, metadata, and aggregate-cost helpers. It adds finite cash, deterministic equal-slot sizing, concurrent-position and exposure entry constraints, daily marks, a cash ledger, and portfolio-valid analytics without changing `NEXT_OPEN_FIXED_HOLD` v1 or the cost-model version. See [the Phase 6 portfolio/risk specification](portfolio_risk_engine.md).
