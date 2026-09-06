# AlphaDesk technical feature specification

## Contract

`ALPHADESK_CORE_TECHNICAL` version `1` is an immutable, daily, on-demand feature set. A formula or semantic change requires a new feature or feature-set version. Results are derived from persisted OHLCV and corporate-action records; feature-value rows are not persisted.

The default adjustment policy is `ADJUSTED`; callers can explicitly request `RAW`. Rates and percentages are returned as decimal fractions (`0.052` means 5.2%). RSI uses a 0–100 scale. Ratios, prices, share counts, currency values, and booleans retain their natural units. Numeric calculations use Python `Decimal`; unavailable or mathematically undefined values are JSON `null`, never NaN or infinity.

All rolling windows count observed price rows, not calendar days. A missing session is neither filled nor silently imputed. The engine loads the complete available prefix before a requested start date, computes warm-up state, and emits only the requested range.

## Point-in-time and availability policy

- Every observation is stamped `available_at` at that exchange calendar's session close in `Asia/Kolkata`. If an exact calendar entry or close is unavailable, the conservative fallback is 23:59:59 local time.
- `as_of` must include a timezone offset. Rows with `available_at > as_of` are excluded, so a future simulator can enforce `available_at <= simulation_time` directly.
- Corporate actions independently require their own `available_at <= as_of`. The repository selects the latest eligible append-only revision before applying the ex-date horizon; later corrections cannot alter an earlier request clock.
- Adjusted results are snapshotted by corporate-action regime. A split or bonus rebases the history used on and after its ex-date, but never rewrites feature results already emitted for earlier observations.
- Adjusted OHLC and volume use the existing Phase 1 split/bonus mechanics consistently across every multi-field formula. Cash dividends, rights, mergers, and demergers are not guessed, and this is not a dividend-total-return series.
- The response identifies feature-set code/version, dataset code/version/provider, adjustment policy, compute time, observation coverage, and a deterministic SHA-256 request-horizon fingerprint that includes selected action revision and availability inputs.
- Missing expected trading sessions are returned as quality warnings. They do not cause interpolation or synthetic observations.

## Formula catalog

| Codes | Definition | Minimum observations |
| --- | --- | ---: |
| `RET_1D`, `RET_5D`, `RET_10D`, `RET_20D` | `close[t] / close[t-N] - 1`; N is an observation lag | 2, 6, 11, 21 |
| `MOM_1M_21D`, `MOM_3M_63D`, `MOM_6M_126D` | `close[t] / close[t-N] - 1` for N = 21, 63, 126 | 22, 64, 127 |
| `SMA_20`, `SMA_50`, `SMA_100`, `SMA_200` | Arithmetic mean of the latest N closes, including T; requires exactly N valid observations | 20, 50, 100, 200 |
| `EMA_20`, `EMA_50` | `alpha = 2/(N+1)`; seed at T=N-1 with the arithmetic mean of the first N closes, then `alpha*close[t] + (1-alpha)*EMA[t-1]` | 20, 50 |
| `DISTANCE_SMA_20`, `DISTANCE_SMA_50`, `DISTANCE_SMA_200` | `close[t] / SMA_N[t] - 1`; null when the SMA is unavailable or zero | 20, 50, 200 |
| `RSI_14` | Wilder RSI. Seed average gain/loss with the arithmetic mean of the first 14 close changes, then `(13*previous + current)/14` | 15 |
| `TRUE_RANGE` | `max(high-low, abs(high-prev_close), abs(low-prev_close))`; first observation uses `high-low` | 1 |
| `ATR_14` | Arithmetic mean of the first 14 valid true ranges, then Wilder `(13*ATR[t-1] + TR[t])/14` | 14 |
| `ATR_PCT_14` | `ATR_14 / close`; null for zero/invalid close | 14 |
| `VOLATILITY_20`, `VOLATILITY_60` | Sample standard deviation (`ddof=1`) of N daily simple returns, multiplied by `sqrt(252)` | 21, 61 |
| `AVG_VOLUME_20`, `AVG_VOLUME_50` | Arithmetic mean of the latest N volumes, including T | 20, 50 |
| `VOLUME_RATIO_20`, `VOLUME_RATIO_50` | `volume[t] / AVG_VOLUME_N[t]`; null for zero/invalid average | 20, 50 |
| `AVG_TRADED_VALUE_20` | Arithmetic mean of 20 valid traded-value observations; null if any constituent is missing | 20 |
| `GAP_PCT_1D` | `open[t] / close[t-1] - 1`; null for the first row or zero prior close | 2 |
| `INTRADAY_RETURN` | `close[t] / open[t] - 1`; null for zero open | 1 |
| `RANGE_PCT` | `(high[t] - low[t]) / close[t]`; null for zero close | 1 |
| `CLOSE_LOCATION` | `(close[t] - low[t]) / (high[t] - low[t])`; null when high equals low | 1 |
| `ABOVE_SMA_20`, `ABOVE_SMA_50`, `ABOVE_SMA_200` | `close[t] > SMA_N[t]`; descriptive boolean, null until its SMA exists | 20, 50, 200 |
| `SMA_20_ABOVE_50`, `SMA_50_ABOVE_200` | Moving-average ordering; descriptive boolean, not a signal | 50, 200 |

RSI edge cases are explicit: zero loss with positive gain returns 100; zero gain with positive loss returns 0; both zero returns 50.

## API

- `GET /api/v1/features/catalog`
- `GET /api/v1/feature-sets`
- `GET /api/v1/securities/{symbol}/features`

The security query accepts `start_date`, `end_date`, `feature_set`, `feature_set_version`, `adjustment_policy=adjusted|raw`, and timezone-aware `as_of`. Unknown securities and unsupported feature-set versions return 404. Invalid ranges, adjustment policies, or naive `as_of` values return 422.

## Persistence decision

Feature definitions and feature sets remain versioned, immutable code contracts; results are calculated on demand. Phase 3.6 adds corporate-action knowledge-time columns/revision links, not persisted feature values. Dataset provenance comes from `data_ingestion_runs` plus a deterministic request-horizon fingerprint. Representative PostgreSQL measurements do not justify a materialized feature store.
