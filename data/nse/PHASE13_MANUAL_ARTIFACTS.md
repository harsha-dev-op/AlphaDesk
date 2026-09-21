# Phase 13 official manual artifacts

Status: **RELIANCE FUNDAMENTALS ACTIVATED; OTHER FUNDAMENTALS AND CLASSIFICATION NOT ACTIVATED**

Use only operator-downloaded official files from NSE or NSE Indices. Keep them in Git-ignored `data/nse/inbox/`. Do not scrape unofficial sites, bypass source protections, or invent publication timestamps.

## Financial results

Official page: `https://www.nseindia.com/companies-listing/corporate-filings-financial-results`

For each of `RELIANCE`, `TCS`, `INFY`, `HDFCBANK`, and `ICICIBANK`, download the official Financial Results listing CSV plus each linked XBRL instance needed for the target periods. Also retain the matching official taxonomy package from NSE's **XBRL Filing Information** page. Put the unedited downloads below `data/nse/inbox/phase13/`; raw files are Git-ignored.

The listing CSV alone contains filing metadata, not statement facts, and must not be presented as fundamental activation. Phase 13C added a reviewed, RELIANCE-only adapter for the actual NSE integrated-financial XBRL instances encountered on 2026-09-20. They are ordinary XBRL XML (not inline-XBRL HTML), use the SEBI `in-capmkt` 2025/2026 taxonomy namespaces, and carry direct numeric facts with `contextRef`, `unitRef`, and `decimals` attributes. The adapter accepts only the verified `OneD`, `FourD`, and `OneI` primary contexts; dimensioned disclosure contexts remain excluded.

The official listing contained 12 RELIANCE rows. The six locally supplied consolidated XBRL instances matched exactly by official archive filename and were imported; the six standalone instances were not supplied and were not fabricated or fetched. The adapter verifies symbol, ISIN, scope, audit state, official archive host, broadcast timestamp, context periods, units, raw file checksum, and exact listing-to-file linkage. Unknown concepts remain stored with nullable normalized concepts and raw QName/context provenance.

Use the reviewed bundle command for this exact source family:

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.ingestion.nse.cli fundamentals-ixbrl phase13 --symbol RELIANCE --dry-run
.\.venv\Scripts\python.exe -m app.ingestion.nse.cli fundamentals-ixbrl phase13 --symbol RELIANCE
```

The NSE archive served an exact listing-linked XML normally during the Phase 13C verification. An optional conservative helper can therefore fill missing XBRL files from the operator-supplied listing. It does not discover listing metadata, scrape pages, bypass access controls, or overwrite local files:

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.ingestion.nse.cli fundamentals-ixbrl-download phase13 --symbol RELIANCE
```

Always run the separate dry-run import afterward. A successful download is not import approval.

Production import is permitted only after a clean dry-run with no rejected linked filings. The Phase 13C activation imported six consolidated filings and 1,018 facts; 92 facts matched the explicit v1 concept registry and 926 remained auditable but unmapped. A repeated import inserted zero facts and reported all 1,018 unchanged. No raw artifact was edited.

The generic deterministic CSV contract remains available for future reviewed source-family adapters. Do not hand-edit an official download to resemble this contract.

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
