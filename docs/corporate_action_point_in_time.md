# AlphaDesk corporate-action point-in-time contract

## Event-time semantics

Corporate actions are append-only knowledge records. Their dates are not interchangeable:

| Field | Meaning |
| --- | --- |
| `ex_date` | Economic market date on which a supported action changes the adjustment regime. |
| `announcement_date` | Informational calendar date reported by the source. It is not normally an eligibility timestamp. |
| `source_published_at` | Timezone-aware vendor/public publication time, populated only when the source timestamp is trustworthy. |
| `ingested_at` | Timezone-aware local receipt time. |
| `available_at` | Authoritative historical eligibility time: trustworthy `source_published_at`, otherwise `ingested_at`. |
| `supersedes_action_id` | Optional pointer from a correction to the prior addressable record. |

New ORM records default `ingested_at` to the receipt clock. If `source_published_at` exists, `available_at` is set to that exact time; otherwise it defaults to `ingested_at`. Provider ingestion must preserve this rule. The fictional demo is an explicit legacy exception: because it has no real source timestamp, it uses 23:59:59 `Asia/Kolkata` on `announcement_date` as a conservative deterministic fixture value. That fallback is reproducible test data, not a claim about historical truth.

## Migration and legacy backfill

Alembic revision `c9a4d7e2f631` adds the two availability timestamps, the self-revision key, a `(security_id, available_at)` index, a no-self-reference check, a unique-successor constraint, and a restricted self foreign key.

Existing rows are backfilled transactionally. Rows with a source timestamp use it; legacy rows with an announcement date use 23:59:59 `Asia/Kolkata` on that date; rows lacking both use their original `ingested_at`. The backfill is deliberately conservative and deterministic, but cannot reconstruct publication truth that was never stored.

## Revision selection

Repository reads first require `available_at <= as_of`. Within that eligible set, a row referenced by an eligible successor is replaced by the successor; a future, unavailable correction cannot hide the earlier record. Resolution occurs before the `ex_date` horizon filter so a correction that moves an event beyond the observation horizon cannot leave the superseded ex-date active.

The original row remains directly addressable by UUID. Revision results are sorted by `(ex_date, available_at, id)`. The database prevents self-revisions and branching successors; the resolver rejects eligible cycles and cross-security links rather than applying ambiguous adjustments. There is no update API that mutates prior corporate-action rows.

## Technical-engine and scanner behavior

Both single-security feature requests and `compute_latest_batch` pass their timezone-aware `as_of` into the same repository gate. Only the latest eligible revision can reach `PriceAdjustmentService`; supported split/bonus factors still affect prices strictly before the selected action's `ex_date`. RAW calculation semantics are unchanged.

Dataset fingerprints now include the selected action UUID, predecessor UUID, dates, ratios, cash amount, source, publication time, and availability time. Repeating the same request clock is stable, an unavailable future revision has no effect, and a newly eligible correction produces a different fingerprint.

The contract does not invent historical publication times, support mutable in-place correction, or add dividend/rights/merger adjustment formulas.
