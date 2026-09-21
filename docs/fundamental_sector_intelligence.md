# Phase 13A fundamental, sector, and relative-strength foundation

## Status and scope

Phase 13A added the backend architecture. Phase 13C activated official RELIANCE fundamentals from six operator-downloaded consolidated NSE integrated-financial XBRL instances. Data Health therefore reports RELIANCE fundamentals from their genuine filing timestamps; industry classification and sector benchmarks remain unavailable until qualifying official files are imported. No unofficial source, generated history, or inferred sector benchmark is used.

## Point-in-time filing model

`fundamental_filings` is append-only. Every version retains its security, period, frequency, scope, audit state, source filing identity, source submission timestamp, `available_at`, source artifact, parser version, fingerprint, and optional predecessor. Historical queries require `available_at <= as_of`; period end is never used as an availability proxy. An eligible revision supersedes its predecessor only after the revision itself becomes available.

`fundamental_facts` retains source concepts even when no normalized mapping exists. Its normalized concept is nullable, and missing values remain null. Consolidated and standalone filings are queried separately. Consolidated is preferred; standalone fallback is explicit and never combined with consolidated facts.

The versioned `ALPHADESK_FUNDAMENTAL_CONCEPTS` v1 registry uses explicit aliases only. It does not fuzzy-match labels.

## Metrics

`ALPHADESK_FUNDAMENTAL_METRICS` v1 implements revenue/PAT/EPS year-over-year growth, revenue/PAT/EPS TTM, net margin TTM, debt-to-equity, ROE TTM, FCF TTM, and PE TTM. Each result includes its status, unavailable reason, scope, periods, version, and as-of instant.

TTM accepts exactly four distinct, non-overlapping independent quarterly facts. YTD, annual, overlapping, implausibly long, missing, or insufficient periods return `UNAVAILABLE`; they are never summed as quarters. PE uses the latest market close knowable at the requested as-of instant. PB, ROCE, dividend yield, and interest coverage are intentionally deferred because the initial reliable concept set does not support them without assumptions.

## Classification and relative strength

`security_industry_classifications` stores append-only NSE Indices snapshots with the official four-level hierarchy: macro-economic sector, sector, industry, and basic industry. A snapshot is visible only from its genuine snapshot/retrieval boundary and is never backcast. Peer lists use the latest eligible snapshot per security.

`ALPHADESK_MARKET_RELATIVE_STRENGTH` v1 defines RS as security close-to-close price return minus official NIFTY 200 close-to-close price return over 21, 63, or 126 common sessions. Percentiles are deterministic within eligible current-universe members. The calculation is descriptive, not predictive and is not labeled total return. DEMO equity prices are never combined with the OFFICIAL benchmark.

Sector-relative architecture reuses `indices` and `index_daily_prices`. Classification rows may reference an explicit official sector index. Without such a mapping, sector benchmark status is `UNAVAILABLE`; AlphaDesk never invents a proxy.

## APIs and operator workflow

The read-only API surface is:

- `GET /api/v1/fundamentals/metadata`
- `GET /api/v1/securities/{symbol}/fundamentals?as_of=&scope=`
- `GET /api/v1/securities/{symbol}/fundamental-metrics?as_of=`
- `GET /api/v1/securities/{symbol}/classification?as_of=`
- `GET /api/v1/securities/{symbol}/relative-strength?as_of=`
- `GET /api/v1/securities/{symbol}/research-summary?as_of=`

The lightweight research summary composes identity, latest eligible market data, and truthful fundamental/classification/relative-strength statuses. It produces no AI conclusion.

Official artifacts remain local and Git-ignored. Follow [`data/nse/PHASE13_MANUAL_ARTIFACTS.md`](../data/nse/PHASE13_MANUAL_ARTIFACTS.md) for dry-run commands and required schemas.

## Phase 13C RELIANCE activation boundary

The encountered NSE files are raw XBRL instances rather than inline-XBRL HTML. The RELIANCE-only adapter validates the official listing-to-file link, official archive hostname, NSE identity, consolidated scope, audit state, broadcast timestamp, supported SEBI taxonomy namespace, primary contexts, units, and checksums before normalizing facts into the existing append-only filing model. It preserves raw QName, namespace, context, decimals, unit, file locator, and artifact checksum metadata. Unsupported concepts remain auditable and unmapped rather than being guessed.

As of 2026-09-20, six consolidated filings and 1,018 facts are activated for RELIANCE. Latest-quarter revenue, PAT, and EPS and the supported YoY/TTM metrics are available point-in-time. Debt-to-equity is unavailable because the reviewed registry has no total-borrowings fact for these filings; FCF TTM is unavailable because the source cash-flow facts do not provide four independent quarters. Standalone filings, other issuers, classification, sector benchmarks, and any inferred or synthesized facts remain outside this activation.
