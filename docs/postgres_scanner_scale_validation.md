# AlphaDesk Phase 3.6 PostgreSQL scanner scale validation

## Outcome

**Architecture decision: B — small performance hardening is sufficient.**

The first representative run narrowly missed the 500-member API target: its repeated warm median was 11.132 seconds. Profiling isolated full SQLAlchemy entity construction for 150,000 price rows as the largest avoidable cost. The batch repository now projects only the nine fields required by technical computation into a small slotted read record. No query semantics, calculator, indicator version, price-adjustment rule, cache, snapshot, or feature store changed.

After that hardening, all three engineering targets passed. Version-keyed persistent scanner snapshots are not justified by this workload.

## Guarded environment and fixture

Every setup and cleanup write was preceded by the project-local sanitized connection probe and was accepted only for local PostgreSQL `alphadesk_dev` as user `alphadesk`. The measured runtime was PostgreSQL 17.11 with CPython 3.12.14.

The reusable harness is `app.benchmarks.postgres_scanner_scale`. It creates only a reserved `XADB` / `P36B####` / `P36BENCH###` / `ALPHADESK_SYNTHETIC_BENCH` namespace:

| Record | Count |
| --- | ---: |
| Securities | 500 |
| Sessions per security | 300 |
| Daily prices | 150,000 |
| Corporate actions | 50 |
| Historical universes | 5 |
| Membership rows | 761 |
| Trading-calendar rows | 300 |
| Ingestion runs | 1 |

Inputs are deterministic, fictional Decimal OHLCV histories with security-specific trend, cycle, and volume variation. Every tenth security has a source-timestamped 2-for-1 split. The observation is 2025-02-24 at the 15:30 `Asia/Kolkata` close. All scans use ADJUSTED prices, all 36 authoritative version-1 features, and three registry-backed filters: `RET_20D > -1`, `RSI_14 between 0 and 100`, and `AVG_VOLUME_20 >= 0`.

Setup is idempotent: the second setup returned `ALREADY_PRESENT` with identical counts. Partial or colliding namespaces are refused. Cleanup uses explicit expected symbols/provider/exchange and deletes no rows outside that namespace.

## Final measurements

Direct-service medians use four instrumented runs and exclude the first run. API values for 1–200 are warmed in-process samples. The 500 API result is a separate four-sample series whose warm median excludes its first sample.

| Members | Price rows | Queries | Warm service median | Warm incl. serialization | API | Target | Result |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 300 | 7 | 9.997 ms | 10.043 ms | 18.685 ms | — | reference |
| 10 | 3,000 | 7 | 52.439 ms | 52.539 ms | 55.484 ms | — | reference |
| 50 | 15,000 | 7 | 265.585 ms | 265.874 ms | 246.536 ms | ≤2 s | pass |
| 200 | 60,000 | 19 | 2,904.301 ms | 2,908.371 ms | 2,764.016 ms | ≤5 s | pass |
| 500 | 150,000 | 43 | 7,074.738 ms | 7,082.664 ms | 7,040.164 ms warm median | ≤10 s | pass |

The 500 direct warm range was 7.018–7.283 seconds. The dedicated 500 API warm range was 6.809–7.222 seconds. Every direct and API repetition returned 500 matched rows and fingerprint `8bf9d0ac88ba5aad5c37fda1dda1391c1381959bcde6a08cb269a6bafaca5e26`.

The query count remained exactly `3 + 4 × ceil(member_count / 50)`: universe, historical membership, ingestion provenance, then prices/actions/calendar entries/calendar days per bounded chunk. No N+1 or duplicated corporate-action read appeared.

## Profiling

For the final 500-member instrumented runs, median stage times were approximately:

| Stage | Median |
| --- | ---: |
| Price retrieval including transfer/read-record construction/grouping | 2,431 ms |
| Price SQL cursor execution | 183 ms |
| Price transfer/read-record construction/grouping residual | 2,253 ms |
| Split/bonus adjustment | 1,287 ms |
| Latest technical calculation | 1,685 ms |
| Decimal-to-feature point conversion | 498 ms |
| Dataset fingerprints | 679 ms |
| Corporate-action retrieval | 31 ms |
| Calendar entry/range reads | 15 / 19 ms |

The post-change 500-member `cProfile` run was intentionally diagnostic and slower than a latency sample. Its top cumulative project work was scanner service 11.121 s, batch technical service 11.055 s, projected price retrieval 3.539 s, latest calculation 2.935 s, adjustment 2.304 s, and dataset fingerprinting 0.942 s. SQL cursor time is a small fraction of price retrieval; transfer, Python row creation, adjustment, and exact Decimal calculations now form the expected dynamic cost.

## Reproduction and cleanup

From `backend`, after the sanitized probe reports the exact development identity and migrations are at head:

```powershell
python -m app.benchmarks.postgres_scanner_scale setup
python -m app.benchmarks.postgres_scanner_scale run --runs 4
python -m app.benchmarks.postgres_scanner_scale api --runs 4 --sizes 500
python -m app.benchmarks.postgres_scanner_scale cleanup
```

The completed run removed all 500 securities, 150,000 prices, 50 actions, five indices, 761 memberships, 300 calendar rows, and the one synthetic ingestion run. A post-cleanup status check found zero namespaced records, while the original four-security demo remained available.

## Decision boundary

Option B is selected because a measured, bounded repository hardening moved the representative dynamic scanner under every target while preserving deterministic output. Option A does not describe the initial result because the untouched path missed the repeated 500-member API target. Option C is unnecessary because persistent snapshots are not required to meet the present 300-session targets.

This result is local engineering evidence, not a concurrency or long-history guarantee. Reconsider a version-keyed snapshot only if a future legitimate workload—especially materially longer histories, concurrent requests, or stricter latency objectives—misses its target after profiling. Phase 3.6 adds no strategies, signals, rankings, backtests, ML, broker access, trading, or frontend features.
