# AlphaDesk Phase 6 capital-aware portfolio and risk engine

## Purpose and boundary

Phase 6 turns the immutable Phase 4 setup stream and the Phase 5 execution/cost assumptions into one deterministic, finite-capital historical portfolio. It remains an offline research simulation: it does not recommend securities, optimize parameters, rebalance, connect to brokers, paper trade, or place orders.

The implementation is deliberately separate from the strategy and backtest registries. `LONG_ONLY_EQUAL_SLOT_PORTFOLIO` version `1` owns allocation/accounting policy; Phase 4 still owns setup rules, and `NEXT_OPEN_FIXED_HOLD` version `1` plus `INDIA_NSE_CASH_DELIVERY_2026_09` version `1` still own execution and transaction-cost semantics.

## Architecture and reuse

```text
Portfolio API / UI
    -> PortfolioService
        -> BacktestService.prepare_research
            -> historical membership, calendar, authoritative feature series
            -> immutable Phase 4 strategy evaluation and setup fingerprints
        -> PortfolioSimulator
            -> shared Phase 5 slippage, intraday-exit, action, and cost primitives
        -> portfolio analytics and bounded response previews
```

The preparation extraction is behavior-preserving: the Phase 5 service calls the same `prepare_research` path before its independent-trade simulator. The controlled Phase 5 regression run retained the exact config, dataset, run, setup, trade, and first-trade fingerprints. No strategy definition, feature formula, feature registry, execution profile, or cost-model version changed.

Runs are on demand. Phase 6 adds no table, Alembic migration, cache, snapshot, feature store, Redis service, or saved run.

## Portfolio policy and request controls

The frozen policy registry initially contains only `LONG_ONLY_EQUAL_SLOT_PORTFOLIO` version `1`:

- initial capital defaults to ₹1,000,000;
- maximum concurrent positions defaults to 10;
- maximum entry weight defaults to 10%;
- maximum gross entry exposure defaults to 100%;
- minimum cash reserve defaults to 0%;
- positions are long-only and unlevered;
- entry quantities are positive whole shares;
- one position per security may be open at a time;
- there is no pyramiding or periodic rebalancing.

All effective strategy, profile, cost, policy, capital, capacity, exposure, reserve, risk-free, date, and OOS inputs are validated and included in deterministic configuration identity. Hard limits are 500 historical securities, 3,000 trading sessions, a 10-year calendar range, 5,000 returned position details, and 10,000 returned cash-ledger events.

## Daily event order

Each valid exchange session is processed in this exact order:

1. apply eligible split/bonus actions before the open;
2. process scheduled/delayed and gap-triggered open exits;
3. calculate session-start cash, marked gross value, and equity;
4. sort that session's candidates by symbol (then stable security/candidate identity);
5. allocate and enter accepted candidates at the RAW open with adverse buy slippage;
6. process intraday stop/target exits under the Phase 5 conservative same-bar rule;
7. mark remaining positions at that session's RAW close.

Cash and capacity released by an open exit are available for a new entry at that same open. Cash released by an intraday exit is not retroactively available to entries already considered at the open. No outcome, future return, ranking, or profitability statistic affects tie-breaking.

## Allocation and affordability

At session start:

```text
target slot       = session-start equity / maximum concurrent positions
position cap      = maximum position weight × session-start equity
gross room        = maximum gross exposure × session-start equity - current gross value
reserve floor     = minimum cash reserve × session-start equity
spendable cash    = current cash - reserve floor
candidate budget  = min(target slot, position cap, gross room, spendable cash)
```

The accepted quantity is the largest integer `q >= 1` for which slipped entry price × `q`, rounded entry principal, and the exact Phase 5 buy-leg charges all fit the candidate budget. A deterministic binary search avoids an estimated-cost sizing shortcut. A candidate that cannot buy one share receives `MAX_POSITION_WEIGHT_LIMIT`, `GROSS_EXPOSURE_LIMIT`, or `INSUFFICIENT_PORTFOLIO_CASH`, according to the binding constraint. Other explicit reasons cover occupied security, full capacity, unavailable next-open price, and entry outside the test range.

Weight and exposure limits are entry constraints. The simulator does not sell or rebalance a winning position merely because subsequent price movement raises its observed weight or gross exposure; such drift is surfaced as a warning. The unlevered invariant still requires gross value not to exceed portfolio equity beyond rounding tolerance.

## Cash ledger and position accounting

The append-only in-memory ledger uses typed events:

- `INITIAL_CAPITAL`;
- `ENTRY_PRINCIPAL` and `ENTRY_COST`;
- `EXIT_PROCEEDS` and `EXIT_COST`.

Every event records gross amount, cost amount, signed net cash change, resulting cash, related position/security, sequence, and fingerprint. Cash is quantized to paise and must never be negative. The final running cash must equal the sum of every ledger change or the run fails with a typed portfolio invariant error.

Every position retains strategy/signal/profile/cost/policy identity, raw and slipped prices, integer entry quantity, post-action current quantity, entry/exit turnovers and costs, levels, corporate-action effects, realized results, warnings, and a deterministic fingerprint. Split and bonus processing reuses the Phase 5 revision-aware action routine: it changes quantity and inverse per-share references without creating cash or changing economic exposure. A ratio that creates a fractional post-action entitlement remains a Decimal research quantity; broker cash-in-lieu settlement is not modeled.

Normal end-of-range positions are closed at the last available RAW close inside the requested range and receive adverse sell slippage and costs. A bounded exit with no eligible positive price remains explicitly `EXIT_PRICE_UNAVAILABLE`, with null realized fields, rather than inventing a fill.

## Daily marks and point-in-time safety

Open positions are marked to the current session's positive RAW close. If it is missing, the most recent price already seen for that position is reused and `STALE_MARK_PRICE:<SYMBOL>` is surfaced. A later close is never searched or backfilled. The daily snapshot contains cash, gross market value, equity, cumulative realized P&L, net unrealized P&L, costs, positions, exposure, cash percentage, return, drawdown, stale-mark count, and warnings.

Phase 6 inherits the authoritative point-in-time pipeline:

- historical index membership is evaluated on each signal date;
- EOD observations must be available at the exchange close used for that decision;
- prices after the request horizon cannot affect setups, allocation, or prior marks;
- corporate actions are gated by `available_at` at each decision/session clock;
- append-only action revisions cannot alter pre-availability signals;
- RAW/ADJUSTED feature policy remains explicit, while execution and marks use RAW prices;
- dataset identity includes membership, calendar, price, eligible action-revision, feature-set, and security-series inputs.

Future price, future action, future revision, and stale-mark isolation all have explicit portfolio regression coverage.

## Portfolio analytics

Daily portfolio return is `equity_t / equity_(t-1) - 1`. Statistics use the complete daily curve and complete position set, even when detail previews are truncated.

- total return: `ending equity / initial capital - 1`;
- CAGR: `(ending equity / starting equity)^(365.2425 / elapsed calendar days) - 1`;
- annualized volatility: sample standard deviation (`ddof=1`) of daily returns × `sqrt(252)`;
- daily risk-free rate: `(1 + annual effective risk-free rate)^(1/252) - 1`;
- Sharpe: mean daily excess return / sample standard deviation of daily excess return × `sqrt(252)`;
- Sortino: mean daily excess return × `sqrt(252)` / `sqrt(mean(min(excess, 0)^2))`;
- drawdown: daily equity / running peak equity - 1;
- Calmar: CAGR / absolute maximum drawdown;
- turnover: gross executed buy plus sell turnover / average daily equity;
- cost drag: total modeled transaction costs / gross executed turnover.

Drawdown details include amount, percentage, peak, trough, first recovery, and session duration. Undefined denominators and short samples return null with explicit warnings. Exposure/cash/position statistics and realized position win/return statistics are descriptive only; they are not rankings or recommendations.

## Out-of-sample boundary

`out_of_sample_start_date` is reporting-only. The portfolio, cash, ledger, positions, and action state continue across the boundary without reset. Positions opened before the boundary may exit after it. In-sample and OOS rows are calculated from the actual daily equity snapshots in their respective date ranges; no training, fitting, parameter search, or optimization occurs.

## APIs and response safety

`GET /api/v1/portfolio/metadata` returns the immutable portfolio policy, compatible Phase 5 profiles, costs, strategies, universes, parameter bounds, allocation/metric definitions, hard limits, latest observation, and research disclaimer.

`POST /api/v1/portfolio/run` returns normalized inputs; strategy/profile/cost/policy/dataset metadata; config/dataset/run fingerprints; setup, accepted, rejected, and closed counts; full portfolio/risk and cost analytics; optional OOS segments; all daily snapshots; deterministic bounded position/ledger/rejection previews; warnings; and stage timings.

Unknown identifiers/versions return 404. Invalid parameters or policy/profile combinations return 422. Internal exceptions and stack traces are not serialized. Preview limits do not change full-run aggregate results or run identity.

## Frontend

`/portfolio` is a responsive, read-only research workspace. It provides metadata-driven strategy and execution controls plus capital, position, weight, gross exposure, reserve, and risk-free controls. Results include summary/risk cards, equity and drawdown charts, exposure/cash charts, cost breakdown, auditable positions, rejected candidates, OOS metrics, cash ledger, provenance, warnings, and explicit truncation labels. Loading, metadata failure, run failure, no-run, and no-result states use the existing terminal design system.

## PostgreSQL benchmark

The guarded local PostgreSQL 17.11 benchmark reused the deterministic Phase 3.6 `XADB` namespace and its 300-session, 50/200/500-security cases. Three direct service runs were taken per size; the table reports the median of the last two including serialization, followed by one warmed in-process API request. Each case generated a large permissive Breakout candidate stream while leaving registry defaults unchanged.

| Members | Price rows | Setups | Accepted | Rejected | Queries | Warm service + serialization | API sample | Target | Result |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 50 | 15,000 | 5,656 | 130 | 5,526 | 6 | 1,291.182 ms | 1,247.275 ms | ≤8 s | pass |
| 200 | 60,000 | 23,497 | 130 | 23,367 | 12 | 4,844.065 ms | 4,830.563 ms | ≤20 s | pass |
| 500 | 150,000 | 59,057 | 130 | 58,927 | 24 | 12,462.122 ms | 12,492.066 ms | ≤35 s | pass |

Every repeated run at a given size produced the same run fingerprint. The query formula is `4 + 2 × ceil(member_count / 50)`: index lookup, overlapping membership, calendar, ingestion provenance, then one projected price and action-revision read per bounded 50-security chunk. It has no per-security or per-session N+1 shape. At 500 members, approximate warm service stages were 3.42–3.45 s data loading, 5.81–5.82 s feature calculation, 1.67–1.68 s setup generation, 1.39–1.42 s portfolio allocation/accounting, and about 2 ms risk analytics. The authoritative technical series remains the largest stage; candidate generation/allocation is the next material work as the permissive fixture creates 59,057 setups.

No formula shortcut, cache, snapshot, persistence, strategy tuning, or new feature was introduced for these numbers. The implementation optimization is structural reuse of the already-batched Phase 5 preparation path, bounded response previews, and one in-memory portfolio pass. Approximate peak memory was not reported because a stable low-overhead Windows process metric was unavailable. The fixture was created only after the sanitized `alphadesk_dev` identity guard and removed afterward; all seven namespace counts returned to zero.

Run the read-only measurement only after guarded fixture setup:

```powershell
python -m app.benchmarks.postgres_portfolio --runs 3 --sizes 50 200 500
```

Setup and cleanup remain owned by `app.benchmarks.postgres_scanner_scale`; each write requires the project-local identity guard.

## Known limitations and next boundary

- Daily bars cannot reconstruct intraday path; Phase 5's conservative stop-first ambiguity rule remains.
- Fills model RAW bar prices, deterministic slippage, and configured cash-delivery costs, not liquidity, market impact, partial fills, taxes at investor-account scope, or broker contract notes.
- Unsupported actions and fractional corporate-action cash settlement remain explicit limitations.
- Gross/weight constraints apply at entry; there is no rebalance or forced de-risking after price drift.
- A bounded missing exit may remain unclosed; its market value stays in daily equity but realized metrics remain null.
- Runs are synchronous and on demand. Persistence, saved experiments, caches, feature stores, concurrent-load work, and distributed jobs are absent.
- Multi-strategy capital competition, shorting, leverage, derivatives, ranking, optimization, ML, broker integration, paper trading, and live trading are not implemented.

The recommended next phase is a versioned multi-strategy research-composition and saved-experiment provenance layer that can arbitrate shared capital across already-immutable strategy streams without optimization, brokerage connectivity, or live execution. It should remain separate from Phase 4 strategy definitions and preserve all Phase 5/6 policy versions.
