# Phase 13 official manual artifacts

Status: **ARCHITECTURE READY; PRODUCTION DATA NOT ACTIVATED**

Use only operator-downloaded official files from NSE or NSE Indices. Keep them in Git-ignored `data/nse/inbox/`. Do not scrape unofficial sites, bypass source protections, or invent publication timestamps.

## Financial results

Source: NSE Corporate Filings / Financial Results. The normalized import CSV must retain the official source filing identifier and timezone-aware submission/broadcast timestamp. Required columns are:

`Symbol, Series, ISIN, Source Filing ID, Filing Type, Reporting Frequency, Period Start, Period End, Fiscal Year, Scope, Audit Status, Submission Timestamp, Concept, Value, Unit, Scale, Fact Kind, Value Nature`

Optional columns are `Supersedes Filing ID` and `Fiscal Quarter`.

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.ingestion.nse.cli fundamentals official_financial_results.csv --as-of YYYY-MM-DD --dry-run
.\.venv\Scripts\python.exe -m app.ingestion.nse.cli fundamentals official_financial_results.csv --as-of YYYY-MM-DD
```

Import only when security identities and timestamps validate. Unknown concepts may remain auditable but unmapped. Missing values must remain blank, not zero.

## Industry classification

Source: official NSE Indices industry classification. Required columns are:

`Symbol, Series, ISIN, Macro Economic Sector, Sector, Industry, Basic Industry`

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.ingestion.nse.cli classifications official_industry_classification.csv --as-of YYYY-MM-DD --dry-run
.\.venv\Scripts\python.exe -m app.ingestion.nse.cli classifications official_industry_classification.csv --as-of YYYY-MM-DD
```

The supplied as-of date is the genuine snapshot date. AlphaDesk records truthful retrieval/import availability and never extends the snapshot backward.
