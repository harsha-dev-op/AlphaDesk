# Official/public NSE source audit

Audit date: 2026-09-14. Phase 8 uses only zero-cost files made public by NSE or NSE Indices. AlphaDesk does not use private endpoints, CAPTCHA/anti-bot workarounds, paid subscriptions, broker APIs, or credentialed feeds.

## Selected source contracts

| AlphaDesk artifact | Official/public source | Expected form | Automation policy | Point-in-time limit |
| --- | --- | --- | --- | --- |
| `SECURITY_MASTER` | [NSE All Reports](https://www.nseindia.com/all-reports), `CM - MII - Security File` | `NSE_CM_security_DDMMYYYY.csv.gz` | One conservative direct request or local inbox import | Dated current master; it is not a complete symbol-history archive |
| `EOD_BHAVCOPY` | [NSE All Reports](https://www.nseindia.com/all-reports), `CM-UDiFF Common Bhavcopy Final` | `BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip` | Serial, bounded daily requests or local inbox import | Trade date is authoritative; download time is provenance, not exchange publication time |
| `NIFTY_200_CONSTITUENTS` | [Nifty 200](https://www.niftyindices.com/indices/equity/broad-based-indices/nifty-200) | Current constituent CSV | Direct public file or local inbox import | Current snapshot only, effective from the explicitly supplied as-of date |
| `NIFTY_500_CONSTITUENTS` | [Nifty 500](https://www.niftyindices.com/indices/equity/broad-based-indices/nifty-500) | Current constituent CSV | Direct public file or local inbox import | Current snapshot only, effective from the explicitly supplied as-of date |
| `CORPORATE_ACTIONS` | [NSE Corporate Actions](https://www.nseindia.com/companies-listing/corporate-filings-actions) | Operator-downloaded CSV | Local inbox import | Public table normally lacks a trustworthy publication timestamp; such rows are quarantined |
| `TRADING_HOLIDAYS` | [NSE Trading Holidays](https://www.nseindia.com/resources/exchange-communication-holidays) | Operator-downloaded CSV | Local inbox import | Only explicitly listed dates become holidays; absence never proves closure |

NSE's [forms and formats page](https://www.nseindia.com/static/resources/forms-formats-members) publishes the UDiFF catalogue, CM bhavcopy format, and sample archive. AlphaDesk's `NSE_CM_UDIFF_BHAVCOPY` parser contract is version `1.0.0`, tied to the audited UDiFF v4 layout and its named fields rather than CSV column positions.

## Explicitly avoided sources

- NSE historical/EOD subscriptions and NSE corporate-data subscriptions are paid products and are not integrated.
- Historical Nifty constituent archives are not inferred from today's free snapshot. If an official historical membership source is unavailable at zero cost, AlphaDesk reports `HISTORICAL_MEMBERSHIP_COVERAGE_INCOMPLETE`.
- Public security-wise web screens are not scraped as an alternate bulk API. The published daily UDiFF artifact is the price source.
- No unofficial mirror, finance portal, broker account, cloud account, or commercial dataset is a fallback.

## Responsible-access policy

`OfficialHttpClient` permits HTTPS only and restricts requests and redirects to `nseindia.com`, `nsearchives.nseindia.com`, and `niftyindices.com`. Requests are serial, identify AlphaDesk as a personal-research EOD importer, enforce a minimum spacing interval, use bounded timeouts and at most five attempts, cap `Retry-After` at 60 seconds, and retry only network failures, HTTP 429, and HTTP 5xx. HTTP 403 stops immediately with a local-import instruction; AlphaDesk never rotates identities, solves challenges, or bypasses provider controls.

The normal test suite has no network access. Live checks are separate and operator initiated. A backfill invocation is capped at 93 calendar days, skips weekends and confirmed holidays, and resumes past already successful artifacts.

## Data use and retention

NSE's [data usage and sharing policy](https://www.nseindia.com/static/market-data/nse-data-policy) governs exchange data. Public website availability is not treated as permission to redistribute bulk data. Raw files are retained only under the project-local ignored `data/nse/raw/` directory for reproducibility, and AlphaDesk exposes bounded metadata—not raw downloads—through its API.

Downloaded content is untrusted. The importer caps compressed and expanded sizes, restricts MIME types/extensions, validates safe names and ZIP member paths, rejects nested/traversal members and malformed CSV, and never executes or shells out to an artifact.

## Corporate-action honesty

Only mechanically unambiguous `BONUS n:d` and face-value split descriptions are candidates for the existing adjustment engine. An action is promoted only when the source record provides a timezone-aware publication timestamp. `available_at` equals that source timestamp exactly. No download time, ex-date, record date, or local ingest time is fabricated as point-in-time knowledge.

Unsupported dividends and ambiguous ratios are recorded as warnings and do not enter split/bonus adjustments. Differing revisions are quarantined until an explicit append-only `supersedes_action_id` relationship can be established.
