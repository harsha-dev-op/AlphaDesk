# AlphaDesk Phase 3 market scanner

> PostgreSQL scanner validation is complete for the legitimate three-member demo universe. See [the Phase 3.5 validation report](postgres_scanner_validation.md) for the sanitized connection result, migrations, seed, API smoke checks, timings, profiling, and architecture decision.

> Representative 1/10/50/200/500-member PostgreSQL validation is now complete. See [the Phase 3.6 scale report](postgres_scanner_scale_validation.md) for the guarded synthetic fixture, final timings, profiles, cleanup, and option-B decision.

## Purpose and architecture

The Market Scanner is a read-only point-in-time research service. It resolves the members of a historical index, obtains authoritative technical values from the Phase 2/2.5 engine, applies typed predicates, and returns deterministic results with provenance.

```text
scanner metadata/scan API
          ↓
    ScannerService
      ↙       ↘
historical     TechnicalFeatureService.compute_latest_batch
membership          ↓
repository      existing v1 feature registry/calculators
      ↘             ↙
       bounded repository reads
                ↓
           PostgreSQL
```

The scanner does not copy indicator formulas. `TechnicalFeatureService.compute_latest_batch` remains authoritative for feature values, warm-ups, adjustment behavior, availability times, feature versions, dataset provenance, and input fingerprints.

## API

`GET /api/v1/scanner/metadata` returns the available historical universes, the latest stored price date, supported operators, and scanner-compatible features derived from the versioned technical registry. The frontend uses this response rather than hard-coding feature names.

`POST /api/v1/scanner/scan` accepts one to twenty filters. A representative request is:

```json
{
  "universe": "ALPHADESK_DEMO",
  "observation_date": "2025-03-14",
  "as_of": "2025-03-14T16:00:00+05:30",
  "feature_set": "core",
  "feature_set_version": 1,
  "adjustment_policy": "ADJUSTED",
  "logic": "AND",
  "filters": [
    {"feature": "RSI_14", "operator": "<", "value": 30},
    {"feature": "SMA_20", "operator": ">=", "value": 100},
    {"feature": "AVG_VOLUME_20", "operator": "between", "value": 100000, "upper_value": 5000000}
  ]
}
```

Feature keys are normalized to uppercase. Numeric features support `>`, `>=`, `<`, `<=`, `=`, and inclusive `between`. Boolean features support `=` only. Conditions use AND semantics. Null, unavailable, or insufficient-history feature values never match a predicate. Invalid features, values, operators, ranges, feature sets, and versions return typed validation errors.

Each result includes the security UUID, symbol and name, exact observation date, scanner as-of timestamp, matching feature values, feature versions, feature-set code and version, adjustment policy, availability time, dataset code/version/provider, dataset fingerprint, and input fingerprint. The response also has a deterministic scan fingerprint over the request, members, and input datasets. Results are ordered case-insensitively by symbol and then security UUID; UI sorting is deterministic as well.

## Point-in-time rules

- Membership is resolved with the existing inclusive `valid_from`/`valid_to` historical membership query at `observation_date`.
- `observation_date` must be on or before the scanner as-of date in the as-of timestamp's timezone.
- The feature engine's query horizon is capped at both the requested observation date and the local as-of date.
- A security is eligible only when the engine emits a feature row for the exact requested observation date. A prior row is never substituted.
- Every used feature row must satisfy `available_at <= as_of`.
- Adjusted calculations require corporate-action `available_at <= as_of`, select the latest eligible append-only revision, and retain the established action-regime behavior. A future action or correction cannot rebase an earlier request clock.
- Calendar lookup, raw price history, ingestion provenance, and technical calculations remain in the established service/repository layers.

Corporate actions distinguish economic `ex_date`, informational `announcement_date`, trustworthy `source_published_at`, local `ingested_at`, authoritative `available_at`, and `supersedes_action_id`. See [the corporate-action point-in-time contract](corporate_action_point_in_time.md). Legacy/demo announcement-date fallback values are explicitly deterministic approximations, not reconstructed historical truth.

## Database query behavior

Scanner reads are bounded and avoid per-security queries. One scan uses three fixed queries for universe lookup, historical membership, and ingestion provenance, followed by four queries per batch of at most 50 securities for projected ordered prices, availability/revision-gated actions, latest relevant calendar rows, and exchange trading days:

```text
query count = 3 + 4 × ceil(member_count / 50)
```

This gives seven SELECTs for 1, 10, or 50 members, nineteen for 200, and forty-three for 500. Empty universes return without feature-batch reads.

## Isolated scanner benchmark

The reproducible manual benchmark is `python -m app.benchmarks.market_scanner`. It uses deterministic in-memory SQLite data so it cannot touch a runtime PostgreSQL database. Setup time is excluded. Each security has 2,500 daily rows and two corporate actions; each ADJUSTED scan applies three matching conditions (`RET_20D > -1`, `RSI_14 between 0 and 100`, and `AVG_VOLUME_20 >= 0`). Values below are one-run local measurements and are not a PostgreSQL production capacity claim.

| Securities | Data rows | Conditions | Results | Queries | Universe | DB cursor execution | Technical compute + materialization | Predicate | Response build | Serialization | Total incl. serialization |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 2,500 | 3 | 1 | 7 | 4.677 ms | 0.443 ms | 83.930 ms | 0.100 ms | 0.086 ms | 0.675 ms | 89.881 ms |
| 10 | 25,000 | 3 | 10 | 7 | 7.655 ms | 0.751 ms | 804.143 ms | 0.060 ms | 0.185 ms | 0.151 ms | 812.879 ms |
| 50 | 125,000 | 3 | 50 | 7 | 8.621 ms | 1.272 ms | 9,969.124 ms | 0.376 ms | 1.494 ms | 0.850 ms | 9,981.969 ms |
| 200 | 500,000 | 3 | 200 | 19 | 44.594 ms | 2.570 ms | 30,945.118 ms | 1.622 ms | 4.115 ms | 2.591 ms | 31,001.354 ms |

The database cursor column measures statement execution, not ORM row fetch/hydration, which is included in technical compute and materialization. The dominant cost is loading/materializing full histories plus exact recursive adjusted computation; predicate evaluation and response serialization are negligible. A 50-security RAW comparison completed in 7,390.882 ms with seven queries. A projected-column experiment made the 50-security ADJUSTED run slower (10,618 ms), so it was reverted. Indicator semantics were not weakened.

The 500-security case was not run: it would construct 1.25 million ORM rows and the 200-security case already took about 31 seconds after setup. The command supports `--counts 500` for an appropriately provisioned environment.

## Persistence decision and limitations

No Redis, external cache, feature store, or scanner snapshot table was added. Phase 3.6 measured PostgreSQL with 300 sessions per security. The initial repeated 500-member API median was 11.132 seconds; projecting the batch price fields reduced the final warm API median to 7.040 seconds and met all 50/200/500 targets. The selected outcome is **B — small performance hardening is sufficient**.

Phase 3 intentionally supports historical index universes, exact-date technical filters, and a flat AND list only. It does not provide OR/grouping, ranking, saved scans, alerts, intraday data, live feeds, backtesting, recommendations, strategies, portfolios, brokers, orders, or execution. Feature output is limited to registered Phase 2 technical features and their established daily availability contract.

## Recommendation

Keep the dynamic architecture and narrow batch read. Reconsider version-keyed snapshots only after a future legitimate workload—longer histories, concurrency, or tighter objectives—misses a defined target under profiling. The current evidence does not justify persistence.
