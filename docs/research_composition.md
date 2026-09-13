# AlphaDesk Phase 7 research composition and saved experiments

## Purpose and boundary

Phase 7 evaluates multiple immutable Phase 4 strategy definitions together under one deterministic policy and optionally saves the exact normalized research configuration plus append-only execution provenance. It remains EOD research. It does not rank securities, predict returns, recommend trades, allocate capital, invoke the Phase 5 backtester or Phase 6 portfolio engine, or create orders.

```text
historical membership + one as-of clock
                    ↓
authoritative ALPHADESK_STRATEGY_TECHNICAL v1 batch computation
                    ↓
shared union of component feature dependencies
                    ↓
immutable Phase 4 condition evaluation for every component
                    ↓
CONSENSUS_N_OF_M v1
          ↙                     ↘
ephemeral response       explicit saved experiment
                                  ↓
                        append-only replay runs
```

## Immutable composition registry

`CONSENSUS_N_OF_M` version 1 is a frozen, slotted policy definition in a separate read-only registry keyed by `(policy_code, policy_version)`. Import-time validation rejects duplicate versions and invalid component bounds. It permits 2–10 distinct strategy code/version pairs, requires `1 <= N <= M`, and canonicalizes components by strategy code, strategy version, and normalized parameter representation. A behavior change requires a new policy version.

The composition registry does not alter the strategy registry. All components must resolve through the authoritative Phase 4 registry, and overrides pass through the existing strict boolean/Decimal parameter validation. Unknown strategies, versions, and parameters are rejected; no executable expressions or generic rule language are stored.

## N-of-M and missing-history semantics

For each historical universe member, Phase 7 counts independently matched and insufficient-history components:

- `MATCHED` when `matched_count >= N`.
- `INSUFFICIENT_FEATURE_HISTORY` when the threshold is not met but `matched_count + insufficient_count >= N`.
- `NOT_MATCHED` otherwise.

Every historical member is returned in symbol order. A missing technical value remains `null`, makes that component insufficient, and is never converted to zero or a normal false condition. Component audit records contain the strategy/version, normalized parameters, condition values, pass states, definition/result fingerprints, and warnings.

## Shared feature computation

The service resolves the set union of all component feature dependencies, validates that an authoritative registered feature set covers it, and calls `TechnicalFeatureService.compute_latest_batch` exactly once. The current three strategies produce a 13-feature union covered by `ALPHADESK_STRATEGY_TECHNICAL` v1. The technical engine may calculate its registered frame internally, but market history, actions, calendars, and dataset provenance are each loaded only through this one bounded batch path.

The 50-security chunking contract is unchanged. There are no per-security or per-strategy SQL reads, caches, snapshots, Redis services, feature stores, or background jobs.

## Single point-in-time clock

Every component shares the same universe, historical membership date, timezone-aware as-of timestamp, adjustment policy, and EOD trading-calendar availability rule:

- Membership is resolved at `observation_date` with inclusive validity intervals.
- `observation_date` cannot be later than the IST date represented by `as_of`.
- Price reads are capped at the requested historical horizon.
- The requested date must have an exact row whose exchange-close `available_at <= as_of`.
- Corporate actions require `available_at <= as_of`; only the latest eligible append-only revision is used.
- Actions beyond the observation horizon are excluded.
- RAW and ADJUSTED are explicit configuration inputs and distinct provenance states.

Future prices, future-ex-date actions, future-available revisions, and later membership changes cannot alter an earlier run. A historically eligible data correction does alter the dataset fingerprint and is reported during replay.

## Deterministic fingerprints

Canonical JSON uses sorted mappings, UTC-normalized datetimes, ISO dates, normalized Decimal strings, stable symbol order, and canonical component order.

- `composition_config_fingerprint` binds policy code/version and the fully normalized request: strategies, versions, effective parameters, N, universe, observation date, as-of, and RAW/ADJUSTED mode. It is invariant to selection order and equivalent Decimal forms.
- `composition_dataset_fingerprint` binds the historical universe UUID/date/clock/mode and each ordered member's eligible technical-input fingerprint.
- `composition_run_fingerprint` binds config, dataset, engine/version provenance, and ordered composition-result fingerprints.

Execution timestamps and timings are excluded. Component result fingerprints use the shared Phase 4 fingerprint helper, preserving the existing strategy result identity contract.

## Saved experiment model

Migration `4f7a9c2d1e60` adds two tables.

### `research_experiments`

An immutable definition with UUID, name, optional description, policy code/version, canonical normalized request JSON, config fingerprint, and creation timestamp. No update or delete API exists, and ORM guards reject update/delete operations.

### `research_experiment_runs`

An append-only record with UUID, experiment FK, execution timestamp, replay status, optional reference-run FK, request snapshot, config/dataset/run fingerprints, engine provenance JSON, aggregate summary JSON, compact ordered member-outcome JSON, and warnings JSON. Indexes support experiment history order plus config, dataset, and run fingerprint lookup. Foreign keys use restrictive deletion behavior.

The compact member snapshot stores symbol, composition status/counts/fingerprint, and each component's strategy/version/status/strategy fingerprint/result fingerprint. It does not duplicate OHLCV, corporate actions, the market database, or reconstructable registry condition trees.

## Replay and drift

Replay reads the immutable definition, evaluates it through the current authoritative engine, appends a new run, and compares it with the latest prior run:

- `REPRODUCED`: config, dataset, and run fingerprints match.
- `DATASET_DRIFT_DETECTED`: config matches but historically eligible data provenance changed.
- `ENGINE_OR_RESULT_DRIFT_DETECTED`: config and dataset match but engine provenance or outcomes changed.
- A config-fingerprint mismatch is an internal invariant violation and no replay record is appended.

Replay verifies reproducibility; it does not promise that an external data vendor will never revise history. Prior runs are never overwritten.

## API

| Method | Route | Behavior |
| --- | --- | --- |
| GET | `/api/v1/research/compositions/metadata` | Policies, strategies, parameters, universes, modes, and disclaimer |
| POST | `/api/v1/research/compositions/evaluate` | Pure on-demand composition; persists nothing |
| POST | `/api/v1/research/experiments` | Saves an immutable definition and its initial run |
| GET | `/api/v1/research/experiments` | Bounded page of definitions with latest run summary |
| GET | `/api/v1/research/experiments/{id}` | Definition and latest run |
| GET | `/api/v1/research/experiments/{id}/runs` | Bounded, oldest-first immutable run history |
| POST | `/api/v1/research/experiments/{id}/runs` | Exact replay and append-only run creation |

List page sizes are limited to 100. Schemas use typed UUIDs, strict bounded fields, explicit statuses, and normalized identifiers. Domain validation maps to 422, unknown registry/experiment resources to 404, invariant failures to 409, and sanitized persistence failures to 503. SQL details, connection strings, and stack traces are not returned.

## Frontend workflow

`/research` adds Composition and Experiments views to the existing terminal shell. The builder provides metadata-driven policy/version, historical universe, IST date/as-of, RAW/ADJUSTED mode, 2–10 strategy selections, versioned strategy parameters, and N. Results show aggregate status counts, all members, per-component state, expandable condition audits, warnings, and all three fingerprints.

Saving is explicit and uses the normalized request attached to the displayed evaluation, not mutable builder state. Experiments display immutable configuration, UUID/fingerprint provenance, latest replay state, run history, compact symbol outcomes, and exact replay controls. The UI uses no BUY/SELL, ranking, probability, confidence, expected-return, or fake real-time claims.

## PostgreSQL benchmark

The guarded Phase 3.6 synthetic fixture supplied 300 sessions per security with all three Phase 4 strategies, `CONSENSUS_N_OF_M` v1, N=2, and ADJUSTED features. Three direct-service samples were taken; warm medians exclude the first. The API sample followed warmed runs. Every member and all component conditions were serialized.

| Members | Price rows | Component evaluations | SELECTs | Warm service median | Warm incl. serialization | API sample |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 50 | 15,000 | 150 | 7 | 318.841 ms | 320.739 ms | 353.836 ms |
| 200 | 60,000 | 600 | 19 | 1,256.399 ms | 1,263.939 ms | 1,608.465 ms |
| 500 | 150,000 | 1,500 | 43 | 3,250.848 ms | 3,269.516 ms | 3,596.092 ms |

Query count remains `3 + 4 × ceil(member_count / 50)`: universe lookup, historical membership, ingestion provenance, then one projected-price, action, calendar-entry, and calendar-range read per chunk. It does not multiply by three strategies. Feature computation dominates runtime; composition itself is small. The fixture was removed after measurement and all benchmark namespace counts returned to zero.

Run after the guarded fixture setup:

```powershell
python -m app.benchmarks.postgres_research_composition --runs 3 --sizes 50 200 500
```

## Verification coverage

The Phase 7 suite adds 33 tests for registry immutability, component/count/parameter validation, every consensus status branch, 1-of-M and M-of-M thresholds, missing-value handling, all-member ordering, Decimal and component-order invariance, repeatable fingerprints, one shared feature load, the seven-query single-batch contract, future price/action/revision/membership invariance, EOD availability, RAW/ADJUSTED provenance, ephemeral behavior, PostgreSQL-shaped JSON/UUID persistence, immutable definitions, append-only run history, all replay states, invariant protection, pagination, API endpoints, and sanitized 4xx responses. The full suite contains 203 passing tests.

## Limitations and explicit non-goals

Phase 7 is synchronous, daily-bar, point-in-time research over modeled local data. It does not snapshot the historical market database, distribute work, optimize parameters, weight strategies by forecasts, discover strategies, produce rankings or predictions, connect composition to backtests/portfolio allocation, support shorting/leverage/derivatives/intraday execution, connect to brokers, or place paper/live orders. No Redis, Celery, Kafka, WebSocket, feature-store, authentication overhaul, billing, cloud deployment, or comprehensive live NSE ingestion was added.
