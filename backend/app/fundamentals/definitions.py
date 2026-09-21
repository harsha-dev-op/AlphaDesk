from __future__ import annotations

from dataclasses import dataclass


CONCEPT_REGISTRY_CODE = "ALPHADESK_FUNDAMENTAL_CONCEPTS"
CONCEPT_REGISTRY_VERSION = "1"
METRIC_ENGINE_CODE = "ALPHADESK_FUNDAMENTAL_METRICS"
METRIC_ENGINE_VERSION = "1"
RELATIVE_STRENGTH_CODE = "ALPHADESK_MARKET_RELATIVE_STRENGTH"
RELATIVE_STRENGTH_VERSION = "1"
SECTOR_BENCHMARK_MAPPING_CODE = "ALPHADESK_NSE_SECTOR_BENCHMARK_MAPPING"
SECTOR_BENCHMARK_MAPPING_VERSION = "1"


@dataclass(frozen=True, slots=True)
class ConceptDefinition:
    code: str
    statement: str
    fact_kind: str
    aliases: tuple[str, ...]


_CONCEPTS = (
    ConceptDefinition("REVENUE", "INCOME_STATEMENT", "DURATION", ("Revenue", "Revenue From Operations", "Income From Operations")),
    ConceptDefinition("OTHER_INCOME", "INCOME_STATEMENT", "DURATION", ("Other Income",)),
    ConceptDefinition("TOTAL_INCOME", "INCOME_STATEMENT", "DURATION", ("Total Income",)),
    ConceptDefinition("TOTAL_EXPENSES", "INCOME_STATEMENT", "DURATION", ("Total Expenses",)),
    ConceptDefinition("FINANCE_COST", "INCOME_STATEMENT", "DURATION", ("Finance Costs", "Finance Cost")),
    ConceptDefinition("PROFIT_BEFORE_TAX", "INCOME_STATEMENT", "DURATION", ("Profit Before Tax", "Profit Before Tax From Continuing Operations")),
    ConceptDefinition("TAX_EXPENSE", "INCOME_STATEMENT", "DURATION", ("Tax Expense", "Total Tax Expense")),
    ConceptDefinition("PROFIT_AFTER_TAX", "INCOME_STATEMENT", "DURATION", ("Profit After Tax", "Net Profit After Tax", "Profit For The Period", "ProfitLossForPeriod")),
    ConceptDefinition("EPS_BASIC", "INCOME_STATEMENT", "DURATION", ("Basic EPS", "Basic Earnings Per Share", "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations")),
    ConceptDefinition("EPS_DILUTED", "INCOME_STATEMENT", "DURATION", ("Diluted EPS", "Diluted Earnings Per Share", "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations")),
    ConceptDefinition("TOTAL_ASSETS", "BALANCE_SHEET", "INSTANT", ("Total Assets",)),
    ConceptDefinition("TOTAL_EQUITY", "BALANCE_SHEET", "INSTANT", ("Total Equity", "Total Shareholders Equity", "Equity")),
    ConceptDefinition("TOTAL_BORROWINGS", "BALANCE_SHEET", "INSTANT", ("Total Borrowings", "Borrowings")),
    ConceptDefinition("CURRENT_BORROWINGS", "BALANCE_SHEET", "INSTANT", ("Current Borrowings",)),
    ConceptDefinition("NON_CURRENT_BORROWINGS", "BALANCE_SHEET", "INSTANT", ("Non Current Borrowings", "Non-Current Borrowings")),
    ConceptDefinition("CASH_AND_CASH_EQUIVALENTS", "BALANCE_SHEET", "INSTANT", ("Cash And Cash Equivalents",)),
    ConceptDefinition("CASH_FLOW_FROM_OPERATIONS", "CASH_FLOW", "DURATION", ("Cash Flow From Operating Activities", "Net Cash From Operating Activities", "CashFlowsFromUsedInOperatingActivities")),
    ConceptDefinition("CAPITAL_EXPENDITURE", "CASH_FLOW", "DURATION", ("Capital Expenditure", "Purchase Of Property Plant And Equipment", "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities")),
)

CONCEPT_DEFINITIONS = {item.code: item for item in _CONCEPTS}
_ALIASES = {
    "".join(character for character in alias.upper() if character.isalnum()): item.code
    for item in _CONCEPTS
    for alias in (item.code, *item.aliases)
}


def normalize_concept(source_concept: str) -> str | None:
    """Map only explicit aliases; deliberately performs no fuzzy matching."""
    key = "".join(character for character in source_concept.strip().upper() if character.isalnum())
    return _ALIASES.get(key)


METRIC_DEFINITIONS: dict[str, str] = {
    "REVENUE_LATEST_QUARTER": "latest independent quarterly revenue fact",
    "PAT_LATEST_QUARTER": "latest independent quarterly PAT fact",
    "EPS_LATEST_QUARTER": "latest independent quarterly basic EPS fact",
    "REVENUE_YOY": "latest independent quarter revenue / comparable prior-year quarter - 1",
    "PAT_YOY": "latest independent quarter PAT / comparable prior-year quarter - 1",
    "EPS_YOY": "latest independent quarter basic EPS / comparable prior-year quarter - 1",
    "REVENUE_TTM": "sum of four non-overlapping independent quarterly revenue facts",
    "PAT_TTM": "sum of four non-overlapping independent quarterly PAT facts",
    "EPS_TTM": "sum of four non-overlapping independent quarterly basic EPS facts",
    "NET_MARGIN_TTM": "PAT_TTM / REVENUE_TTM",
    "DEBT_TO_EQUITY": "latest total borrowings / latest total equity",
    "ROE_TTM": "PAT_TTM / average of latest and comparable prior-year total equity",
    "FCF_TTM": "cash flow from operations TTM - capital expenditure TTM",
    "PE_TTM": "latest point-in-time market close / EPS_TTM",
}

RS_PERIODS: dict[str, int] = {"RS_1M_21D": 21, "RS_3M_63D": 63, "RS_6M_126D": 126}

# Exact, reviewed NSE Indices sector labels only.  This registry intentionally
# does not perform substring/fuzzy matching: an unfamiliar classification must
# remain unmapped until it is reviewed against the official classification.
SECTOR_BENCHMARK_MAPPINGS: dict[str, str] = {
    "Automobile and Auto Components": "NIFTYAUTO",
    "Fast Moving Consumer Goods": "NIFTYFMCG",
    "Financial Services": "NIFTYFIN",
    "Healthcare": "NIFTYHEALTHCARE",
    "Information Technology": "NIFTYIT",
    "Metals & Mining": "NIFTYMETAL",
    "Oil Gas & Consumable Fuels": "NIFTYOILGAS",
    "Pharmaceuticals": "NIFTYPHARMA",
    "Realty": "NIFTYREALTY",
}


def sector_benchmark_symbol(sector: str) -> str | None:
    """Return a benchmark only for an exact, versioned classification label."""
    return SECTOR_BENCHMARK_MAPPINGS.get(sector.strip())
