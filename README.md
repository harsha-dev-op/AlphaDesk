# AlphaDesk

AlphaDesk is an AI-assisted quantitative research platform for Indian markets. This repository contains the **Phase 5 research foundation**: point-in-time market data, a versioned technical feature engine, the historical Market Scanner, revision-aware corporate actions, deterministic versioned strategy setups, and an offline point-in-time backtesting engine. It does not rank securities, generate recommendations, allocate a portfolio, connect to brokers, or place real or paper orders.

## What is included

- FastAPI with versioned, typed endpoints and structured JSON logging
- SQLAlchemy 2.x models backed by PostgreSQL, with an Alembic initial migration
- Raw OHLCV storage plus non-destructive, availability-gated split and bonus adjustment views
- Point-in-time fundamentals and historical index memberships
- Exchange trading calendar and data-quality checks
- Vendor-neutral `MarketDataProvider` plus deterministic fictional demo data
- Immutable feature and feature-set catalogs with 36 Core v1 features plus two strategy-support price-structure features
- On-demand raw or point-in-time adjusted feature calculation with explicit warm-ups and availability timestamps
- Dataset/version/provider provenance and deterministic request-horizon fingerprints
- Bounded projected multi-security reads and an exact dynamic latest-row path for scanner workloads
- A typed, read-only Market Scanner over historical index membership with generic AND filters and reproducibility fingerprints
- An immutable strategy registry and reusable historical universe evaluator for Momentum Trend, 20-Day Breakout, and Mean-Reversion Pullback v1 research setups
- Typed strategy conditions, validated parameter overrides, EOD availability gates, and deterministic definition/result/evaluation fingerprints
- Efficient historical feature-series replay, a separate immutable backtest-profile registry, and survivorship-safe setup events
- Next-session RAW-open trade simulation with fixed notional, integer entry shares, overlap controls, adverse slippage, optional stops/targets, bounded missing-price handling, and split/bonus continuity
- A versioned India NSE cash-delivery cost model, trade analytics, yearly/OOS stability views, deterministic config/dataset/run fingerprints, and bounded trade previews
- React 19, Vite/Vinext, Tailwind CSS, reusable UI primitives, and responsive financial-operations pages
- Isolated backend tests; no paid APIs or external credentials

The dependency direction is:

```text
frontend → FastAPI routes → backtest/strategy/scanner/technical services → repositories → SQLAlchemy → PostgreSQL
                                  ↘ immutable registries, quality, calendar, adjustments
```

See [architecture notes](docs/architecture.md) for the point-in-time and adjustment conventions.

## Database tables

| Table | Purpose |
| --- | --- |
| `securities` | Stable security master, including listing/delisting lifecycle |
| `daily_prices` | Immutable-by-policy raw daily OHLCV with unique security/session key |
| `corporate_actions` | Dated actions with publication/receipt availability and append-only revision links |
| `indices` | Index definitions |
| `index_memberships` | Inclusive historical membership intervals |
| `fundamental_reports` | Fiscal facts with report and effective availability dates |
| `trading_calendar` | Trading days, holidays, special/closed sessions, and session times |
| `strategy_definitions` | Versioned metadata contract only; no strategy logic |
| `data_ingestion_runs` | Dataset/provider/version audit and last-successful-ingestion status |

## Local setup

Requirements: Python 3.12+, Node.js 22+, and PostgreSQL 15+. Docker is optional.

### 1. Start PostgreSQL

With Docker Compose:

```powershell
Copy-Item .env.example .env
# Change POSTGRES_PASSWORD in .env, then:
docker compose up -d postgres
```

Without Docker, create a local PostgreSQL database/user using your normal PostgreSQL tools. AlphaDesk only needs a valid SQLAlchemy URL; it does not depend on Docker.

Use an isolated database name such as `alphadesk_dev`. Root `.env` requires `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` for Docker Compose. Backend `.env` requires a SQLAlchemy `DATABASE_URL`, for example `postgresql+psycopg://alphadesk:<url-encoded-password>@localhost:5432/alphadesk_dev`. The checked-in examples contain placeholders only; never commit either `.env` file.

### 2. Configure and run the backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit DATABASE_URL in .env so its password matches your PostgreSQL user.
python -m app.benchmarks.postgres_verify --env-file .env
python -m alembic upgrade head
python -m app.data.seed
python -m uvicorn app.main:app --reload --port 8000
```

The safe probe prints only the config file, driver, host, port, database, username, reachability, and sanitized status; it never prints the password. It must report `CONNECTED` before migrations or seed writes. With the API running, verify PostgreSQL-backed feature responses with:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod 'http://localhost:8000/api/v1/securities/ALPHAIND/features?adjustment_policy=adjusted'
Invoke-RestMethod 'http://localhost:8000/api/v1/securities/ALPHAIND/features?adjustment_policy=raw'
```

macOS/Linux activation is `source .venv/bin/activate`; the remaining Python commands are identical.

The seeder is idempotent and inserts clearly marked fictional data: four security-master records, roughly 90 calendar days of OHLCV coverage, split and bonus events, changing demo-index membership, two financial periods per security, and a local exchange calendar.

### 3. Configure and run the frontend

In a second terminal:

```powershell
cd frontend
npm install
Copy-Item .env.example .env.local
npm run dev
```

Open `http://localhost:3000`. The frontend expects the API at `http://localhost:8000` unless `NEXT_PUBLIC_API_URL` says otherwise.

## API

| Method | Route | Notes |
| --- | --- | --- |
| GET | `/health` | API, database, and application version |
| GET | `/api/v1/securities` | `page`, `page_size`, and optional `q` search |
| GET | `/api/v1/securities/{symbol}` | Case-insensitive symbol lookup |
| GET | `/api/v1/securities/{symbol}/prices` | Optional `start_date`, `end_date`, `view=raw|adjusted` |
| GET | `/api/v1/indices` | Available indices |
| GET | `/api/v1/indices/{index}/members` | Optional historical `as_of` date |
| GET | `/api/v1/data-quality/status` | Aggregate plus named validation checks |
| GET | `/api/v1/features/catalog` | Immutable formula, field, window, output, and availability definitions |
| GET | `/api/v1/feature-sets` | Available feature-set codes and versions |
| GET | `/api/v1/securities/{symbol}/features` | `start_date`, `end_date`, set/version, `adjustment_policy`, and timezone-aware `as_of` |
| GET | `/api/v1/scanner/metadata` | Historical universes, operators, and registry-derived filterable features |
| POST | `/api/v1/scanner/scan` | Exact-date, point-in-time historical-universe scan with one to twenty AND filters |
| GET | `/api/v1/strategies/catalog` | Immutable strategy versions, rules, parameters, feature dependencies, and universes |
| POST | `/api/v1/strategies/evaluate` | Read-only exact-date historical-universe strategy setup evaluation |
| GET | `/api/v1/backtests/metadata` | Versioned profiles, costs, strategies, universes, policies, rates, and request limits |
| POST | `/api/v1/backtests/run` | On-demand point-in-time historical setup replay and independent trade simulation |

Interactive API documentation is available at `http://localhost:8000/docs` while the backend is running.

## Frontend design system

The Phase 4 interface uses a persistent, collapsible research-terminal shell with a compact top bar, keyboard-accessible global security search (`Ctrl+K` / `Cmd+K`), grouped navigation, and explicit local-system health. Future modules are visible only as disabled navigation or tabs; the UI does not imply unavailable functionality.

Shared theme variables in `frontend/app/globals.css` define the near-black surface hierarchy, borders, typography colors, restrained cyan selection accent, and semantic success/warning/danger/bullish/bearish states. Reusable shell, page-header, panel, metric, status, loading, empty, and error components live under `frontend/src/components`. Data pages retain compact rows, sticky table headers, horizontal overflow, tabular-number formatting, and visible keyboard focus.

The seven implemented workspaces are Dashboard, Securities, Security Detail (Overview plus Technicals), Market Scanner, Strategy Research, Backtest Research, and Data Health. Backtest Research renders metadata-driven controls, explicit execution/cost assumptions, full-run trade analytics, deterministic trade previews, yearly/OOS results, and a safely labeled return distribution. All values come from AlphaDesk APIs; the UI does not fabricate live prices, signals, rankings, recommendations, or portfolio performance.

See [the technical feature specification](docs/technical_features.md) for every formula, warm-up rule, unit, availability policy, and point-in-time convention.
See [the corporate-action point-in-time contract](docs/corporate_action_point_in_time.md) for timestamp semantics, revision resolution, and legacy backfill policy.
See [the Phase 2.5 performance baseline](docs/performance_baseline.md) for reproducible full-history/latest benchmarks, profiling evidence, and the persistence decision.
See [the Phase 3 Market Scanner specification](docs/market_scanner.md) for filter semantics, point-in-time rules, result provenance, query behavior, scanner benchmarks, and the cache decision.
See [the Phase 3.6 PostgreSQL scale validation](docs/postgres_scanner_scale_validation.md) for the 1/10/50/200/500-member measurements, profiling, guarded fixture lifecycle, and architecture decision.
See [the Phase 4 strategy engine specification](docs/strategy_engine.md) for versioned definitions, rules, parameters, fingerprints, point-in-time behavior, APIs, UI, tests, and PostgreSQL strategy measurements.
See [the Phase 5 backtesting engine specification](docs/backtesting_engine.md) for historical series computation, execution/cost contracts, corporate-action accounting, analytics, fingerprints, APIs, UI, tests, and PostgreSQL measurements.

## Verification

Backend:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m pytest -q
```

Frontend:

```powershell
cd frontend
npm run typecheck
npm run lint
npm run build
```

Tests cover the Phase 1 controls plus technical formulas and warm-ups, adjusted split/bonus continuity, point-in-time availability/revisions, catalogs, versions, batch/latest/historical equivalence, bounded query shape, scanner validation, Phase 4 strategies, and Phase 5 entry/exit/overlap/cost/corporate-action/analytics/OOS/fingerprint/API behavior.

## Phase 5 limitations

- Demo records are fictional and stop in March 2025; their stale-calendar warning is intentional.
- Only mechanically deterministic split and bonus adjustments are calculated. Other corporate actions are stored but require a validated policy in a later phase.
- Features are calculated on demand and are not persisted. The representative PostgreSQL scanner met the 50/200/500 targets after projected price reads, so a version-keyed snapshot remains deliberately deferred.
- Scanner expressions remain a flat AND list and strategy rules are immutable code definitions. There is no OR/grouping, ranking, saved experiment, authentication, streaming feed, intraday strategy behavior, background worker, cache, parameter optimization, ML, portfolio allocator/risk engine, broker, paper-trading loop, or live order model.
- Phase 5 results are independent fixed-notional simulated trades, not a capital-constrained portfolio. Portfolio CAGR, Sharpe, drawdown, exposure, and equity curves are intentionally absent.
- Daily OHLC requires a conservative same-bar stop/target policy. Unsupported corporate actions, market impact, contract-note aggregation, and broker-specific rules beyond explicit overrides remain limitations.
- PostgreSQL is the production target; SQLite is used only by isolated tests and migration smoke checks.

## Architecture decision after Phase 5

The historical engine reuses projected batch reads and authoritative calculator primitives. Its 300-session PostgreSQL benchmark passed the 5/15/30-second 50/200/500 soft targets with 6/12/24 SELECTs and no date-multiplied query growth. No backtest persistence, cache, snapshot, feature store, or migration is justified. The next boundary is a separate capital-aware portfolio/risk simulation phase; it was not started automatically.
