from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Generic, TypeVar

from app.ingestion.nse.artifacts import ArtifactBytes, ArtifactSchemaDriftError, csv_records
from app.ingestion.nse.definitions import ArtifactType, IssueSeverity, SOURCE_DEFINITIONS


T = TypeVar("T")
MAX_RECORDED_ISSUES = 200
SUPPORTED_EQUITY_SERIES = frozenset({"EQ"})
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9&._-]{0,31}$")
ISIN_PATTERN = re.compile(r"^IN[A-Z0-9]{10}$")
MAX_PRICE = Decimal("99999999999999.9999")
MAX_TRADED_VALUE = Decimal("9999999999999999999999.99")


@dataclass(frozen=True, slots=True)
class ParsedIssue:
    severity: IssueSeverity
    code: str
    message: str
    row_number: int | None = None
    row_key: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParseResult(Generic[T]):
    rows: tuple[T, ...]
    issues: tuple[ParsedIssue, ...]
    source_row_count: int
    rejected_row_count: int
    warning_count: int


class _IssueCollector:
    def __init__(self) -> None:
        self.items: list[ParsedIssue] = []
        self.rejected_count = 0
        self.warning_count = 0

    def add(
        self,
        severity: IssueSeverity,
        code: str,
        message: str,
        *,
        row_number: int | None = None,
        row_key: str | None = None,
        metadata: dict[str, object] | None = None,
        rejected: bool = False,
    ) -> None:
        if rejected:
            self.rejected_count += 1
        if severity == IssueSeverity.WARNING:
            self.warning_count += 1
        if len(self.items) < MAX_RECORDED_ISSUES:
            self.items.append(
                ParsedIssue(
                    severity=severity,
                    code=code,
                    message=message,
                    row_number=row_number,
                    row_key=row_key,
                    metadata=metadata or {},
                )
            )


@dataclass(frozen=True, slots=True)
class NormalizedSecurity:
    symbol: str
    series: str
    company_name: str
    isin: str
    listing_date: date | None
    is_active: bool


@dataclass(frozen=True, slots=True)
class NormalizedPrice:
    symbol: str
    series: str
    isin: str | None
    trading_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    traded_value: Decimal | None


@dataclass(frozen=True, slots=True)
class NormalizedConstituent:
    symbol: str
    series: str
    isin: str
    company_name: str
    industry: str | None


@dataclass(frozen=True, slots=True)
class NormalizedCorporateAction:
    symbol: str
    series: str
    purpose: str
    action_type: str
    ex_date: date
    record_date: date | None
    ratio_numerator: Decimal
    ratio_denominator: Decimal
    source_published_at: datetime | None


@dataclass(frozen=True, slots=True)
class NormalizedHoliday:
    trading_date: date
    description: str


def _normalized_header(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _resolve_headers(
    headers: list[str], aliases: dict[str, tuple[str, ...]], *, required: set[str]
) -> dict[str, str]:
    available = {_normalized_header(header): header for header in headers}
    resolved: dict[str, str] = {}
    for canonical, candidates in aliases.items():
        for candidate in candidates:
            original = available.get(_normalized_header(candidate))
            if original is not None:
                resolved[canonical] = original
                break
    missing = sorted(required - resolved.keys())
    if missing:
        raise ArtifactSchemaDriftError(
            "Official artifact schema is missing required columns: " + ", ".join(missing)
        )
    return resolved


def _parse_date(value: str, field_name: str) -> date:
    for pattern in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            continue
    raise ValueError(f"{field_name} is not a supported date")


def _optional_date(value: str, field_name: str) -> date | None:
    if not value.strip() or value.strip() in {"-", "NA", "N/A"}:
        return None
    return _parse_date(value, field_name)


def _decimal(value: str, field_name: str) -> Decimal:
    try:
        parsed = Decimal(value.replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} is not a valid Decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return parsed


def _optional_decimal(value: str, field_name: str) -> Decimal | None:
    if not value.strip() or value.strip() in {"-", "NA", "N/A"}:
        return None
    return _decimal(value, field_name)


def _volume(value: str) -> int:
    parsed = _decimal(value, "volume")
    if parsed != parsed.to_integral_value():
        raise ValueError("volume must be an integer")
    result = int(parsed)
    if result < 0 or result > 2_147_483_647:
        raise ValueError("volume is outside the supported range")
    return result


def _validate_numeric_storage(
    value: Decimal, field_name: str, *, maximum: Decimal, scale: int
) -> None:
    if abs(value) > maximum or value.as_tuple().exponent < -scale:
        raise ValueError(f"{field_name} is outside the supported Decimal range or scale")


def _validate_symbol_and_isin(symbol: str, isin: str) -> None:
    if not SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError("symbol is malformed")
    if not ISIN_PATTERN.fullmatch(isin):
        raise ValueError("ISIN is malformed")


SECURITY_ALIASES = {
    "symbol": ("TckrSymb", "Symbol", "Security Symbol"),
    "series": ("SctySrs", "Series", "Security Series"),
    "company_name": (
        "FinInstrmNm",
        "Company Name",
        "Name of Company",
        "Security Name",
        "Short Name",
        "Name",
    ),
    "isin": ("ISIN", "ISIN Code"),
    "listing_date": ("Date of Listing", "Listing Date", "DtOfListing"),
    "status": ("Normal Market Status", "Security Status", "Status"),
}


def parse_security_master(artifact: ArtifactBytes) -> ParseResult[NormalizedSecurity]:
    headers, source_rows = csv_records(artifact)
    columns = _resolve_headers(
        headers,
        SECURITY_ALIASES,
        required={"symbol", "series", "company_name", "isin"},
    )
    output: list[NormalizedSecurity] = []
    issues = _IssueCollector()
    seen_symbols: set[tuple[str, str]] = set()
    seen_isins: set[str] = set()
    for row_number, row in source_rows:
        symbol = row[columns["symbol"]].upper()
        series = row[columns["series"]].upper()
        isin = row[columns["isin"]].upper()
        row_key = f"{symbol}:{series}"
        if series not in SUPPORTED_EQUITY_SERIES:
            issues.add(
                IssueSeverity.INFO,
                "UNSUPPORTED_EQUITY_SERIES",
                f"Series {series or '<blank>'} is outside the Phase 8 equity policy",
                row_number=row_number,
                row_key=row_key,
                rejected=True,
            )
            continue
        try:
            _validate_symbol_and_isin(symbol, isin)
            company_name = row[columns["company_name"]].strip()
            if not company_name:
                raise ValueError("company name is blank")
            if len(company_name) > 255:
                raise ValueError("company name exceeds 255 characters")
            listing_date = (
                _optional_date(row[columns["listing_date"]], "listing date")
                if "listing_date" in columns
                else None
            )
        except ValueError as exc:
            issues.add(
                IssueSeverity.ERROR,
                "MALFORMED_SECURITY_IDENTIFIER",
                str(exc),
                row_number=row_number,
                row_key=row_key,
                rejected=True,
            )
            continue
        identity = (symbol, series)
        if identity in seen_symbols:
            issues.add(
                IssueSeverity.ERROR,
                "DUPLICATE_SYMBOL_SERIES",
                "Duplicate symbol/series in security artifact",
                row_number=row_number,
                row_key=row_key,
                rejected=True,
            )
            continue
        if isin in seen_isins:
            issues.add(
                IssueSeverity.ERROR,
                "DUPLICATE_ISIN_CONFLICT",
                "Duplicate ISIN in security artifact",
                row_number=row_number,
                row_key=row_key,
                rejected=True,
            )
            continue
        status_value = row[columns["status"]].strip().upper() if "status" in columns else "ACTIVE"
        output.append(
            NormalizedSecurity(
                symbol=symbol,
                series=series,
                company_name=company_name,
                isin=isin,
                listing_date=listing_date,
                is_active=status_value not in {"SUSPENDED", "DELISTED", "INACTIVE"},
            )
        )
        seen_symbols.add(identity)
        seen_isins.add(isin)
    return ParseResult(
        rows=tuple(output),
        issues=tuple(issues.items),
        source_row_count=len(source_rows),
        rejected_row_count=issues.rejected_count,
        warning_count=issues.warning_count,
    )


PRICE_ALIASES = {
    "trade_date": ("TradDt", "Trade Date", "Date"),
    "segment": ("Sgmt", "Segment"),
    "symbol": ("TckrSymb", "Symbol"),
    "series": ("SctySrs", "Series"),
    "isin": ("ISIN", "ISIN Code"),
    "open": ("OpnPric", "Open Price", "Open"),
    "high": ("HghPric", "High Price", "High"),
    "low": ("LwPric", "Low Price", "Low"),
    "close": ("ClsPric", "Close Price", "Close"),
    "volume": ("TtlTradgVol", "Total Traded Quantity", "Volume"),
    "traded_value": ("TtlTrfVal", "Turnover", "Traded Value"),
}


def parse_bhavcopy(
    artifact: ArtifactBytes, *, expected_date: date
) -> ParseResult[NormalizedPrice]:
    headers, source_rows = csv_records(artifact)
    columns = _resolve_headers(
        headers,
        PRICE_ALIASES,
        required={"trade_date", "symbol", "series", "open", "high", "low", "close", "volume"},
    )
    output: list[NormalizedPrice] = []
    issues = _IssueCollector()
    seen: set[tuple[str, str, date]] = set()
    for row_number, row in source_rows:
        series = row[columns["series"]].upper()
        segment = row[columns["segment"]].upper() if "segment" in columns else "CM"
        if segment not in {"CM", "CASH", "CAPITAL MARKET"} or series not in SUPPORTED_EQUITY_SERIES:
            issues.add(
                IssueSeverity.INFO,
                "UNSUPPORTED_EQUITY_SERIES",
                "Row is outside the NSE CM EQ policy",
                row_number=row_number,
                rejected=True,
            )
            continue
        symbol = row[columns["symbol"]].upper()
        row_key = f"{symbol}:{series}:{expected_date.isoformat()}"
        try:
            if not SYMBOL_PATTERN.fullmatch(symbol):
                raise ValueError("symbol is malformed")
            trade_date = _parse_date(row[columns["trade_date"]], "trade date")
            if trade_date != expected_date:
                raise ValueError("row trade date does not match artifact source date")
            open_ = _decimal(row[columns["open"]], "open")
            high = _decimal(row[columns["high"]], "high")
            low = _decimal(row[columns["low"]], "low")
            close = _decimal(row[columns["close"]], "close")
            volume = _volume(row[columns["volume"]])
            traded_value = (
                _optional_decimal(row[columns["traded_value"]], "traded value")
                if "traded_value" in columns
                else None
            )
            for field_name, value in (
                ("open", open_),
                ("high", high),
                ("low", low),
                ("close", close),
            ):
                _validate_numeric_storage(value, field_name, maximum=MAX_PRICE, scale=4)
            if traded_value is not None:
                _validate_numeric_storage(
                    traded_value,
                    "traded value",
                    maximum=MAX_TRADED_VALUE,
                    scale=2,
                )
            if any(value <= 0 for value in (open_, high, low, close)):
                raise ValueError("OHLC values must be positive for an accepted trade row")
            if high < low:
                raise ValueError("high is below low")
            if not low <= open_ <= high:
                raise ValueError("open is outside the low/high range")
            if not low <= close <= high:
                raise ValueError("close is outside the low/high range")
            if traded_value is not None and (not traded_value.is_finite() or traded_value < 0):
                raise ValueError("traded value must be finite and non-negative")
            isin = row[columns["isin"]].upper() if "isin" in columns and row[columns["isin"]] else None
            if isin is not None and not ISIN_PATTERN.fullmatch(isin):
                raise ValueError("ISIN is malformed")
        except ValueError as exc:
            issues.add(
                IssueSeverity.ERROR,
                "INVALID_OHLCV_ROW",
                str(exc),
                row_number=row_number,
                row_key=row_key,
                rejected=True,
            )
            continue
        identity = (symbol, series, trade_date)
        if identity in seen:
            issues.add(
                IssueSeverity.ERROR,
                "DUPLICATE_ARTIFACT_ROW",
                "Duplicate symbol/series/date in EOD artifact",
                row_number=row_number,
                row_key=row_key,
                rejected=True,
            )
            continue
        seen.add(identity)
        output.append(
            NormalizedPrice(
                symbol=symbol,
                series=series,
                isin=isin,
                trading_date=trade_date,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                traded_value=traded_value,
            )
        )
    return ParseResult(
        rows=tuple(output),
        issues=tuple(issues.items),
        source_row_count=len(source_rows),
        rejected_row_count=issues.rejected_count,
        warning_count=issues.warning_count,
    )


CONSTITUENT_ALIASES = {
    "symbol": ("Symbol", "Ticker Symbol"),
    "series": ("Series", "Security Series"),
    "isin": ("ISIN Code", "ISIN"),
    "company_name": ("Company Name", "Security Name"),
    "industry": ("Industry",),
}


def parse_constituents(artifact: ArtifactBytes) -> ParseResult[NormalizedConstituent]:
    headers, source_rows = csv_records(artifact)
    columns = _resolve_headers(
        headers,
        CONSTITUENT_ALIASES,
        required={"symbol", "series", "isin", "company_name"},
    )
    output: list[NormalizedConstituent] = []
    issues = _IssueCollector()
    seen: set[tuple[str, str]] = set()
    for row_number, row in source_rows:
        symbol = row[columns["symbol"]].upper()
        series = row[columns["series"]].upper()
        isin = row[columns["isin"]].upper()
        row_key = f"{symbol}:{series}"
        try:
            _validate_symbol_and_isin(symbol, isin)
            if series not in SUPPORTED_EQUITY_SERIES:
                raise ValueError("constituent is outside the EQ series policy")
            company_name = row[columns["company_name"]].strip()
            if not company_name:
                raise ValueError("company name is blank")
        except ValueError as exc:
            issues.add(
                IssueSeverity.ERROR,
                "INVALID_CONSTITUENT",
                str(exc),
                row_number=row_number,
                row_key=row_key,
                rejected=True,
            )
            continue
        if (symbol, series) in seen:
            issues.add(
                IssueSeverity.ERROR,
                "DUPLICATE_INDEX_MEMBER",
                "Duplicate member in current constituent snapshot",
                row_number=row_number,
                row_key=row_key,
                rejected=True,
            )
            continue
        seen.add((symbol, series))
        output.append(
            NormalizedConstituent(
                symbol=symbol,
                series=series,
                isin=isin,
                company_name=company_name,
                industry=row[columns["industry"]].strip() or None if "industry" in columns else None,
            )
        )
    return ParseResult(
        rows=tuple(output),
        issues=tuple(issues.items),
        source_row_count=len(source_rows),
        rejected_row_count=issues.rejected_count,
        warning_count=issues.warning_count,
    )


ACTION_ALIASES = {
    "symbol": ("Symbol",),
    "series": ("Series",),
    "purpose": ("Purpose",),
    "ex_date": ("Ex-Date", "Ex Date"),
    "record_date": ("Record Date",),
    "published_at": ("Source Published At", "Broadcast Timestamp", "Announcement Timestamp"),
}
BONUS_PATTERN = re.compile(r"\bBONUS(?:\s+ISSUE)?\s*[-:]?\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\b", re.I)
SPLIT_PATTERN = re.compile(
    r"\b(?:SPLIT|SUB[- ]?DIVISION)\b.*?\bFROM\s+(?:RS\.?\s*)?(\d+(?:\.\d+)?)"
    r".*?\bTO\s+(?:RS\.?\s*)?(\d+(?:\.\d+)?)",
    re.I,
)


def _parse_published_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("publication timestamp must include a timezone offset")
    return parsed.astimezone(UTC)


def parse_corporate_actions(artifact: ArtifactBytes) -> ParseResult[NormalizedCorporateAction]:
    headers, source_rows = csv_records(artifact)
    columns = _resolve_headers(
        headers,
        ACTION_ALIASES,
        required={"symbol", "series", "purpose", "ex_date"},
    )
    output: list[NormalizedCorporateAction] = []
    issues = _IssueCollector()
    seen: set[tuple[str, str, date, str]] = set()
    for row_number, row in source_rows:
        symbol = row[columns["symbol"]].upper()
        series = row[columns["series"]].upper()
        purpose = row[columns["purpose"]].strip()
        row_key = f"{symbol}:{series}:{purpose[:80]}"
        if series not in SUPPORTED_EQUITY_SERIES:
            issues.add(
                IssueSeverity.INFO,
                "UNSUPPORTED_EQUITY_SERIES",
                "Corporate action is outside the EQ series policy",
                row_number=row_number,
                row_key=row_key,
                rejected=True,
            )
            continue
        bonus = BONUS_PATTERN.search(purpose)
        split = SPLIT_PATTERN.search(purpose)
        if bonus:
            action_type = "BONUS"
            numerator, denominator = Decimal(bonus.group(1)), Decimal(bonus.group(2))
        elif split:
            from_face, to_face = Decimal(split.group(1)), Decimal(split.group(2))
            if to_face <= 0 or from_face <= to_face or from_face % to_face != 0:
                issues.add(
                    IssueSeverity.WARNING,
                    "AMBIGUOUS_ACTION_RATIO",
                    "Split face-value ratio is not unambiguous",
                    row_number=row_number,
                    row_key=row_key,
                    rejected=True,
                )
                continue
            action_type = "STOCK_SPLIT"
            numerator, denominator = from_face / to_face, Decimal("1")
        else:
            code = "UNSUPPORTED_CORPORATE_ACTION"
            if "SPLIT" in purpose.upper() or "BONUS" in purpose.upper():
                code = "AMBIGUOUS_ACTION_RATIO"
            issues.add(
                IssueSeverity.WARNING,
                code,
                "Action is not an unambiguous supported split or bonus",
                row_number=row_number,
                row_key=row_key,
                rejected=True,
            )
            continue
        try:
            if not SYMBOL_PATTERN.fullmatch(symbol):
                raise ValueError("symbol is malformed")
            ex_date = _parse_date(row[columns["ex_date"]], "ex date")
            record_date = (
                _optional_date(row[columns["record_date"]], "record date")
                if "record_date" in columns
                else None
            )
            published_at = (
                _parse_published_at(row[columns["published_at"]])
                if "published_at" in columns and row[columns["published_at"]].strip()
                else None
            )
            if numerator <= 0 or denominator <= 0 or not all(
                math.isfinite(float(value)) for value in (numerator, denominator)
            ):
                raise ValueError("ratio must be positive and finite")
        except ValueError as exc:
            issues.add(
                IssueSeverity.ERROR,
                "MALFORMED_CORPORATE_ACTION",
                str(exc),
                row_number=row_number,
                row_key=row_key,
                rejected=True,
            )
            continue
        identity = (symbol, action_type, ex_date, purpose.casefold())
        if identity in seen:
            issues.add(
                IssueSeverity.ERROR,
                "DUPLICATE_CORPORATE_ACTION",
                "Duplicate action in artifact",
                row_number=row_number,
                row_key=row_key,
                rejected=True,
            )
            continue
        seen.add(identity)
        output.append(
            NormalizedCorporateAction(
                symbol=symbol,
                series=series,
                purpose=purpose,
                action_type=action_type,
                ex_date=ex_date,
                record_date=record_date,
                ratio_numerator=numerator,
                ratio_denominator=denominator,
                source_published_at=published_at,
            )
        )
        if published_at is None:
            issues.add(
                IssueSeverity.WARNING,
                "AVAILABILITY_TIMESTAMP_UNKNOWN",
                "Recognized action is quarantined because the artifact has no trustworthy publication timestamp",
                row_number=row_number,
                row_key=row_key,
                rejected=True,
            )
    return ParseResult(
        rows=tuple(output),
        issues=tuple(issues.items),
        source_row_count=len(source_rows),
        rejected_row_count=issues.rejected_count,
        warning_count=issues.warning_count,
    )


HOLIDAY_ALIASES = {
    "date": ("Date", "Trading Date", "Holiday Date"),
    "description": ("Description", "Holiday", "Reason"),
}


def parse_holidays(artifact: ArtifactBytes) -> ParseResult[NormalizedHoliday]:
    headers, source_rows = csv_records(artifact)
    columns = _resolve_headers(headers, HOLIDAY_ALIASES, required={"date", "description"})
    output: list[NormalizedHoliday] = []
    issues = _IssueCollector()
    seen: set[date] = set()
    for row_number, row in source_rows:
        try:
            trading_date = _parse_date(row[columns["date"]], "holiday date")
            description = row[columns["description"]].strip()
            if not description:
                raise ValueError("holiday description is blank")
        except ValueError as exc:
            issues.add(
                IssueSeverity.ERROR,
                "MALFORMED_HOLIDAY",
                str(exc),
                row_number=row_number,
                rejected=True,
            )
            continue
        if trading_date in seen:
            issues.add(
                IssueSeverity.ERROR,
                "DUPLICATE_HOLIDAY",
                "Duplicate holiday date in artifact",
                row_number=row_number,
                row_key=trading_date.isoformat(),
                rejected=True,
            )
            continue
        seen.add(trading_date)
        output.append(NormalizedHoliday(trading_date=trading_date, description=description))
    return ParseResult(
        rows=tuple(output),
        issues=tuple(issues.items),
        source_row_count=len(source_rows),
        rejected_row_count=issues.rejected_count,
        warning_count=issues.warning_count,
    )


def parser_identity(artifact_type: ArtifactType) -> tuple[str, str]:
    definition = SOURCE_DEFINITIONS[artifact_type]
    return definition.parser_code, definition.parser_version
