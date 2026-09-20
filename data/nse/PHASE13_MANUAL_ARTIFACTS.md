# Phase 13 official manual artifacts

Status: **IMPLEMENTATION READY; PRODUCTION FUNDAMENTALS AND CLASSIFICATION NOT ACTIVATED**

Use only operator-downloaded official files from NSE or NSE Indices. Keep them in Git-ignored `data/nse/inbox/`. Do not scrape unofficial sites, bypass source protections, or invent publication timestamps.

## Financial results

Official page: `https://www.nseindia.com/companies-listing/corporate-filings-financial-results`

For each of `RELIANCE`, `TCS`, `INFY`, `HDFCBANK`, and `ICICIBANK`, download the official Financial Results listing CSV plus each linked XBRL instance needed for the target periods. Also retain the matching official taxonomy package from NSE's **XBRL Filing Information** page. Put the unedited downloads below `data/nse/inbox/phase13/`; raw files are Git-ignored.

The listing CSV alone contains filing metadata, not statement facts, and must not be presented as fundamental activation. A future source-family adapter must read the official XBRL instance and emit the deterministic import contract below. Do not hand-edit an official download to resemble this contract. Until an encountered taxonomy adapter has been reviewed and tested, run no production import.

The deterministic import contract retains the official source filing identifier and timezone-aware submission/broadcast timestamp. Required columns are:

`Symbol, Series, ISIN, Source Filing ID, Filing Type, Reporting Frequency, Period Start, Period End, Fiscal Year, Scope, Audit Status, Submission Timestamp, Concept, Value, Unit, Scale, Fact Kind, Value Nature`

Optional columns are `Supersedes Filing ID` and `Fiscal Quarter`.

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.ingestion.nse.cli fundamentals ..\data\nse\inbox\phase13\validated_financial_results.csv --as-of YYYY-MM-DD --dry-run
.\.venv\Scripts\python.exe -m app.ingestion.nse.cli fundamentals ..\data\nse\inbox\phase13\validated_financial_results.csv --as-of YYYY-MM-DD
```

Import only when security identities and timestamps validate. Unknown concepts may remain auditable but unmapped. Missing values must remain blank, not zero.

## Industry classification

Official page: `https://www.niftyindices.com/resources/industry-classification`

Download the official company-level classification CSV without editing it to `data/nse/inbox/phase13/official_industry_classification.csv`. The public page currently also exposes classification guidelines and structure documents; those describe the hierarchy but are not a company snapshot and must not be imported as one.

The importer requires these official company-snapshot columns:

`Symbol, Series, ISIN, Macro Economic Sector, Sector, Industry, Basic Industry`

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.ingestion.nse.cli classifications ..\data\nse\inbox\phase13\official_industry_classification.csv --as-of YYYY-MM-DD --dry-run
.\.venv\Scripts\python.exe -m app.ingestion.nse.cli classifications ..\data\nse\inbox\phase13\official_industry_classification.csv --as-of YYYY-MM-DD
```

The supplied as-of date is the genuine snapshot date. AlphaDesk records truthful retrieval/import availability and never extends the snapshot backward.

Validation must reject unresolved symbol/ISIN pairs, non-EQ series, missing hierarchy levels, duplicates, and files that are only classification definitions rather than company assignments. Import only after the dry run is clean.

## Sector index history

Official page: `https://www.niftyindices.com/reports/historical-data`

Download history only for indices required by an exact mapping in `ALPHADESK_NSE_SECTOR_BENCHMARK_MAPPING` v1. Save each unedited artifact below `data/nse/inbox/phase13/`. Phase 13B does not infer a mapping from similar words, and a mapping is not considered activated until its official index exists with official point-in-time history. The existing index-price storage must be reused; no proxy or interpolated session is allowed.
