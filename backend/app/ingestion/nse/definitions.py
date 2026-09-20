from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class ProviderCode(StrEnum):
    NSE_PUBLIC = "OFFICIAL_NSE_PUBLIC"
    NSE_INDICES_PUBLIC = "OFFICIAL_NSE_INDICES_PUBLIC"


class DataOrigin(StrEnum):
    DEMO = "DEMO"
    OFFICIAL_NSE_PUBLIC = "OFFICIAL_NSE_PUBLIC"
    UNKNOWN = "UNKNOWN"


class ArtifactType(StrEnum):
    SECURITY_MASTER = "SECURITY_MASTER"
    EOD_BHAVCOPY = "EOD_BHAVCOPY"
    LEGACY_EOD_BHAVCOPY = "LEGACY_EOD_BHAVCOPY"
    NIFTY_200_CONSTITUENTS = "NIFTY_200_CONSTITUENTS"
    NIFTY_500_CONSTITUENTS = "NIFTY_500_CONSTITUENTS"
    NIFTY_200_INDEX_HISTORY = "NIFTY_200_INDEX_HISTORY"
    CORPORATE_ACTIONS = "CORPORATE_ACTIONS"
    TRADING_HOLIDAYS = "TRADING_HOLIDAYS"


class IngestionStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class IssueSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass(frozen=True, slots=True)
class SourceDefinition:
    artifact_type: ArtifactType
    provider: ProviderCode
    owner: str
    landing_page: str
    artifact_kind: str
    parser_code: str
    parser_version: str
    automation_suitability: str
    point_in_time_limit: str


SOURCE_DEFINITIONS: dict[ArtifactType, SourceDefinition] = {
    ArtifactType.SECURITY_MASTER: SourceDefinition(
        ArtifactType.SECURITY_MASTER,
        ProviderCode.NSE_PUBLIC,
        "National Stock Exchange of India Limited",
        "https://www.nseindia.com/all-reports",
        "CM - MII - Security File (.csv.gz)",
        "NSE_MII_SECURITY",
        "1.0.0",
        "Daily public artifact; direct fetch may be blocked and local import is supported.",
        "Represents the security-master state for its source date, not all historical symbol states.",
    ),
    ArtifactType.EOD_BHAVCOPY: SourceDefinition(
        ArtifactType.EOD_BHAVCOPY,
        ProviderCode.NSE_PUBLIC,
        "National Stock Exchange of India Limited",
        "https://www.nseindia.com/all-reports",
        "CM-UDiFF Common Bhavcopy Final (.csv.zip)",
        "NSE_CM_UDIFF_BHAVCOPY",
        "1.0.0",
        "One public daily artifact; conservative serial fetch with local import fallback.",
        "Trade date is known; the website artifact does not establish a historical publication timestamp.",
    ),
    ArtifactType.LEGACY_EOD_BHAVCOPY: SourceDefinition(
        ArtifactType.LEGACY_EOD_BHAVCOPY,
        ProviderCode.NSE_PUBLIC,
        "National Stock Exchange of India Limited",
        "https://www.nseindia.com/all-reports",
        "CM - Bhavcopy (.csv.zip), officially applicable before 2024-07-08",
        "NSE_CM_LEGACY_BHAVCOPY",
        "1.0.0",
        "Official historical archive path; used only before the UDiFF transition.",
        "Trade date is known; the archive does not establish a historical publication timestamp.",
    ),
    ArtifactType.NIFTY_200_CONSTITUENTS: SourceDefinition(
        ArtifactType.NIFTY_200_CONSTITUENTS,
        ProviderCode.NSE_INDICES_PUBLIC,
        "NSE Indices Limited",
        "https://www.niftyindices.com/indices/equity/broad-based-indices/nifty-200",
        "Current constituent CSV",
        "NSE_INDICES_CONSTITUENTS",
        "1.0.0",
        "Public current-snapshot download; local import fallback is supported.",
        "Current snapshot only; never backfilled before the explicitly supplied as-of date.",
    ),
    ArtifactType.NIFTY_500_CONSTITUENTS: SourceDefinition(
        ArtifactType.NIFTY_500_CONSTITUENTS,
        ProviderCode.NSE_INDICES_PUBLIC,
        "NSE Indices Limited",
        "https://www.niftyindices.com/indices/equity/broad-based-indices/nifty-500",
        "Current constituent CSV",
        "NSE_INDICES_CONSTITUENTS",
        "1.0.0",
        "Public current-snapshot download; local import fallback is supported.",
        "Current snapshot only; never backfilled before the explicitly supplied as-of date.",
    ),
    ArtifactType.NIFTY_200_INDEX_HISTORY: SourceDefinition(
        ArtifactType.NIFTY_200_INDEX_HISTORY,
        ProviderCode.NSE_INDICES_PUBLIC,
        "NSE Indices Limited",
        "https://www.niftyindices.com/reports/historical-data",
        "Historical index OHLC report (JSON/CSV)",
        "NSE_INDICES_HISTORICAL_OHLC",
        "1.0.0",
        "Public report supports conservative serial date-range requests and local import fallback.",
        "Historical rows become knowable to AlphaDesk at truthful retrieval/import time; no publication timestamp is inferred.",
    ),
    ArtifactType.CORPORATE_ACTIONS: SourceDefinition(
        ArtifactType.CORPORATE_ACTIONS,
        ProviderCode.NSE_PUBLIC,
        "National Stock Exchange of India Limited",
        "https://www.nseindia.com/companies-listing/corporate-filings-actions",
        "Corporate Actions CSV",
        "NSE_CORPORATE_ACTIONS",
        "1.0.0",
        "Supported through local official CSV import because no stable bulk URL is assumed.",
        "The public table exposes ex/record dates but ordinarily no trustworthy publication timestamp.",
    ),
    ArtifactType.TRADING_HOLIDAYS: SourceDefinition(
        ArtifactType.TRADING_HOLIDAYS,
        ProviderCode.NSE_PUBLIC,
        "National Stock Exchange of India Limited",
        "https://www.nseindia.com/resources/exchange-communication-holidays",
        "Trading-holiday CSV",
        "NSE_TRADING_HOLIDAYS",
        "1.0.0",
        "Supported through local official CSV import; exchange pages remain the source of truth.",
        "Only explicit listed holidays are closed; a missing artifact never proves a holiday.",
    ),
}


def public_artifact_url(artifact_type: ArtifactType, source_date: date) -> str:
    if artifact_type == ArtifactType.EOD_BHAVCOPY:
        stamp = source_date.strftime("%Y%m%d")
        return (
            "https://nsearchives.nseindia.com/content/cm/"
            f"BhavCopy_NSE_CM_0_0_0_{stamp}_F_0000.csv.zip"
        )
    if artifact_type == ArtifactType.SECURITY_MASTER:
        stamp = source_date.strftime("%d%m%Y")
        return f"https://nsearchives.nseindia.com/content/cm/NSE_CM_security_{stamp}.csv.gz"
    if artifact_type == ArtifactType.LEGACY_EOD_BHAVCOPY:
        month = source_date.strftime("%b").upper()
        file_name = f"cm{source_date:%d}{month}{source_date:%Y}bhav.csv.zip"
        return (
            "https://nsearchives.nseindia.com/content/historical/EQUITIES/"
            f"{source_date:%Y}/{month}/{file_name}"
        )
    if artifact_type == ArtifactType.NIFTY_200_CONSTITUENTS:
        return "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv"
    if artifact_type == ArtifactType.NIFTY_500_CONSTITUENTS:
        return "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
    raise ValueError(f"Direct fetch is not supported for {artifact_type.value}")


def eod_artifact_type(source_date: date) -> ArtifactType:
    """Select the official format that applied on the requested trade date."""
    return (
        ArtifactType.LEGACY_EOD_BHAVCOPY
        if source_date < date(2024, 7, 8)
        else ArtifactType.EOD_BHAVCOPY
    )

