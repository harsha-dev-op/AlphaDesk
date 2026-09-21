from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from app.fundamentals.definitions import normalize_concept
from app.ingestion.nse.artifacts import (
    MAX_COMPRESSED_BYTES,
    ArtifactBytes,
    ArtifactValidationError,
    NseArtifactStore,
    validate_file_name,
)
from app.ingestion.nse.client import OfficialHttpClient


XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
SUPPORTED_TAXONOMY_NAMESPACES = {
    "http://www.sebi.gov.in/xbrl/2025-01-31/in-capmkt",
    "http://www.sebi.gov.in/xbrl/2026-01-31/in-capmkt",
}
SUPPORTED_TAXONOMY_FAMILIES = {
    "http://www.sebi.gov.in/xbrl/2025-01-31/in-capmkt": "SEBI_IN_CAPMKT_2025",
    "http://www.sebi.gov.in/xbrl/2026-01-31/in-capmkt": "SEBI_IN_CAPMKT_2026",
}
PHASE13D_EXCLUDED_FINANCIAL_SYMBOLS = frozenset({"HDFCBANK", "ICICIBANK"})
MANIFEST_FILE_NAME = "fundamentals-xbrl-manifest.json"
DEFAULT_MAX_FILES = 8
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
IST = ZoneInfo("Asia/Kolkata")
ADAPTER_CODE = "NSE_INTEGRATED_FINANCIAL_XBRL"
ADAPTER_VERSION = "2"


@dataclass(frozen=True, slots=True)
class XbrlContext:
    context_id: str
    start: date | None
    end: date
    instant: bool
    dimensions: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ListingRow:
    symbol: str
    company_name: str
    period_end: date
    submission_type: str
    audit_status: str
    scope: str
    xbrl_url: str
    broadcast_at: datetime

    @property
    def xbrl_file_name(self) -> str:
        return Path(urlparse(self.xbrl_url).path).name

    @property
    def source_filing_id(self) -> str:
        stem = Path(self.xbrl_file_name).stem
        parts = stem.split("_")
        sequence = parts[3] if len(parts) > 3 else stem
        return f"NSE_XBRL_{sequence}"


@dataclass(frozen=True, slots=True)
class BundleReport:
    artifact: ArtifactBytes
    listing_file: str
    local_artifacts: tuple[str, ...]
    listing_rows: int
    matched_filings: int
    rejected_filings: tuple[str, ...]
    missing_listing_artifacts: tuple[str, ...]
    context_count: int
    fact_count: int
    mapped_fact_count: int
    unmapped_fact_count: int
    earliest_available_at: datetime | None
    latest_available_at: datetime | None
    scopes: tuple[str, ...]
    audit_statuses: tuple[str, ...]
    supported_taxonomy_families: tuple[str, ...]
    unsupported_taxonomy_namespaces: tuple[str, ...]
    raw_checksums: dict[str, str]

    @property
    def source_date(self) -> date:
        if self.latest_available_at is None:
            raise ArtifactValidationError("No linked filing has an availability timestamp")
        return self.latest_available_at.date()

    def as_dict(self) -> dict[str, object]:
        return {
            "adapter": f"{ADAPTER_CODE} v{ADAPTER_VERSION}",
            "listing_file": self.listing_file,
            "local_artifacts": list(self.local_artifacts),
            "listing_rows_matched_by_symbol": self.listing_rows,
            "filings_matched": self.matched_filings,
            "filings_rejected": len(self.rejected_filings),
            "rejected_reasons": list(self.rejected_filings),
            "listing_rows_without_local_artifacts": list(self.missing_listing_artifacts),
            "contexts": self.context_count,
            "facts": self.fact_count,
            "mapped_facts": self.mapped_fact_count,
            "unmapped_facts": self.unmapped_fact_count,
            "earliest_available_at": self.earliest_available_at,
            "latest_available_at": self.latest_available_at,
            "scopes": list(self.scopes),
            "audit_statuses": list(self.audit_statuses),
            "supported_taxonomy_families": list(self.supported_taxonomy_families),
            "unsupported_taxonomy_namespaces": list(
                self.unsupported_taxonomy_namespaces
            ),
            "raw_checksums": self.raw_checksums,
        }


@dataclass(frozen=True, slots=True)
class DownloadReport:
    listing_file: str
    listing_rows: int
    downloaded: tuple[str, ...]
    already_present: tuple[str, ...]
    skipped_by_limit: tuple[str, ...]
    downloaded_bytes: int
    manifest_file: str
    manifest_sha256: str
    checksums: dict[str, str]

    def as_dict(self) -> dict[str, object]:
        return {
            "listing_file": self.listing_file,
            "listing_rows_matched_by_symbol": self.listing_rows,
            "downloaded": list(self.downloaded),
            "already_present": list(self.already_present),
            "skipped_by_limit": list(self.skipped_by_limit),
            "downloaded_bytes": self.downloaded_bytes,
            "manifest_file": self.manifest_file,
            "manifest_sha256": self.manifest_sha256,
            "checksums": self.checksums,
        }


def _validate_phase_scope(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not normalized:
        raise ArtifactValidationError("XBRL symbol is required")
    if normalized in PHASE13D_EXCLUDED_FINANCIAL_SYMBOLS:
        raise ArtifactValidationError(
            f"Phase 13D excludes financial-sector symbol {normalized}"
        )
    return normalized


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _read_bounded(path: Path) -> bytes:
    if not path.is_file():
        raise ArtifactValidationError(f"Artifact is not a file: {path.name}")
    content = path.read_bytes()
    if not content or len(content) > MAX_COMPRESSED_BYTES:
        raise ArtifactValidationError(f"Artifact is empty or too large: {path.name}")
    return content


def _resolve_bundle(value: str | Path, store: NseArtifactStore) -> tuple[Path, Path]:
    store.ensure()
    inbox = store.inbox.resolve()
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = inbox / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(inbox)
    except ValueError as exc:
        raise ArtifactValidationError("The XBRL bundle must remain under data/nse/inbox") from exc
    directory = resolved if resolved.is_dir() else resolved.parent
    if directory == inbox or directory.parent != inbox:
        raise ArtifactValidationError("The XBRL bundle must be one direct inbox subdirectory")
    csv_files = sorted(directory.glob("*.csv"))
    if resolved.is_file():
        if resolved.suffix.casefold() != ".csv":
            raise ArtifactValidationError("A bundle file argument must identify the listing CSV")
        listing = resolved
    elif len(csv_files) == 1:
        listing = csv_files[0]
    else:
        raise ArtifactValidationError("The XBRL bundle must contain exactly one listing CSV")
    return directory, listing


def _listing_rows(content: bytes, symbol: str) -> list[ListingRow]:
    try:
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        records = [
            {str(key).strip(): (value or "").strip() for key, value in row.items() if key}
            for row in reader
        ]
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ArtifactValidationError("The NSE financial-results listing is malformed") from exc
    required = {
        "SYMBOL", "COMPANY NAME", "QUARTER END DATE", "TYPE OF SUBMISSION",
        "AUDITED / UNAUDITED", "CONSOLIDATED / STANDALONE", "XBRL",
        "BROADCAST DATE/TIME",
    }
    if not records or not required.issubset(records[0]):
        raise ArtifactValidationError("The NSE financial-results listing schema is unsupported")
    selected: list[ListingRow] = []
    for raw in records:
        if raw["SYMBOL"].strip().upper() != symbol:
            continue
        try:
            period_end = datetime.strptime(raw["QUARTER END DATE"], "%d-%b-%Y").date()
            broadcast_at = datetime.strptime(
                raw["BROADCAST DATE/TIME"], "%d-%b-%Y %H:%M:%S"
            ).replace(tzinfo=IST)
        except ValueError as exc:
            raise ArtifactValidationError("The listing contains an invalid period or broadcast timestamp") from exc
        scope = raw["CONSOLIDATED / STANDALONE"].strip().upper()
        audit = raw["AUDITED / UNAUDITED"].replace("-", "").strip().upper()
        if scope not in {"CONSOLIDATED", "STANDALONE"}:
            raise ArtifactValidationError("The listing contains an unsupported filing scope")
        if audit not in {"AUDITED", "UNAUDITED"}:
            raise ArtifactValidationError("The listing contains an unsupported audit state")
        url = raw["XBRL"].strip()
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "nsearchives.nseindia.com":
            raise ArtifactValidationError("The listing contains a non-official XBRL locator")
        selected.append(
            ListingRow(
                symbol=symbol,
                company_name=raw["COMPANY NAME"],
                period_end=period_end,
                submission_type=raw["TYPE OF SUBMISSION"].strip().upper(),
                audit_status=audit,
                scope=scope,
                xbrl_url=url,
                broadcast_at=broadcast_at,
            )
        )
    if not selected:
        raise ArtifactValidationError(f"No listing rows were found for {symbol}")
    return selected


def download_fundamentals_ixbrl(
    value: str | Path,
    *,
    symbol: str,
    scope: str | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    store: NseArtifactStore | None = None,
    client: OfficialHttpClient | None = None,
) -> DownloadReport:
    """Download only exact official XBRL links from an operator-supplied listing."""
    symbol = _validate_phase_scope(symbol)
    normalized_scope = scope.strip().upper() if scope else None
    if normalized_scope not in {None, "CONSOLIDATED", "STANDALONE"}:
        raise ArtifactValidationError("Acquisition scope must be CONSOLIDATED or STANDALONE")
    if not 1 <= max_files <= 100:
        raise ArtifactValidationError("max_files must be between 1 and 100")
    if not 1 <= max_bytes <= 512 * 1024 * 1024:
        raise ArtifactValidationError("max_bytes must be between 1 and 536870912")
    artifact_store = store or NseArtifactStore()
    directory, listing_path = _resolve_bundle(value, artifact_store)
    listing_content = _read_bounded(listing_path)
    listings = [
        item
        for item in _listing_rows(listing_content, symbol)
        if normalized_scope is None or item.scope == normalized_scope
    ]
    if not listings:
        raise ArtifactValidationError("No listing rows matched the requested filing scope")
    by_name: dict[str, ListingRow] = {}
    for item in listings:
        name = validate_file_name(item.xbrl_file_name)
        existing = by_name.get(name)
        if existing is not None and existing.xbrl_url != item.xbrl_url:
            raise ArtifactValidationError(f"Conflicting listing locators for {name}")
        by_name[name] = item

    manifest_path = (directory / MANIFEST_FILE_NAME).resolve()
    prior_entries: dict[str, dict[str, object]] = {}
    if manifest_path.exists():
        try:
            prior = json.loads(_read_bounded(manifest_path).decode("utf-8"))
            prior_entries = {
                str(entry["local_filename"]): entry
                for entry in prior.get("artifacts", [])
            }
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactValidationError("The XBRL acquisition manifest is malformed") from exc

    downloaded: list[str] = []
    already_present: list[str] = []
    skipped_by_limit: list[str] = []
    checksums: dict[str, str] = {}
    manifest_entries: list[dict[str, object]] = []
    downloaded_bytes = 0
    owned_client = client is None
    http = client or OfficialHttpClient()
    try:
        for name in sorted(by_name):
            listing = by_name[name]
            destination = (directory / name).resolve()
            if destination.parent != directory.resolve():
                raise ArtifactValidationError("XBRL destination escaped the bundle directory")
            if destination.exists():
                existing_content = _read_bounded(destination)
                checksum = _sha256(existing_content)
                prior_checksum = str(prior_entries.get(name, {}).get("sha256", ""))
                if prior_checksum and prior_checksum != checksum:
                    raise ArtifactValidationError(
                        f"Existing XBRL checksum does not match its manifest: {name}"
                    )
                already_present.append(name)
                checksums[name] = checksum
                previous = prior_entries.get(name)
                if previous and prior_checksum:
                    manifest_entries.append(previous)
                else:
                    isin = _validate_downloaded_xbrl(existing_content, name, symbol)
                    manifest_entries.append(
                        _manifest_entry(
                            listing,
                            checksum=checksum,
                            byte_count=destination.stat().st_size,
                            download_status="VERIFIED_EXISTING",
                            retry_status="NOT_REQUIRED",
                            parser_status="XBRL_ROOT_AND_IDENTITY_VALIDATED",
                            isin=isin,
                        )
                    )
                continue
            if len(downloaded) >= max_files:
                skipped_by_limit.append(name)
                manifest_entries.append(
                    _manifest_entry(
                        listing,
                        checksum="",
                        byte_count=0,
                        download_status="SKIPPED_BY_FILE_LIMIT",
                        retry_status="NOT_ATTEMPTED",
                        parser_status="NOT_AVAILABLE",
                        isin=None,
                    )
                )
                continue
            response = http.fetch(
                listing.xbrl_url,
                allowed_content_types=frozenset(
                    {"application/xml", "text/xml", "application/octet-stream", "text/plain"}
                ),
            )
            if downloaded_bytes + len(response.content) > max_bytes:
                skipped_by_limit.append(name)
                manifest_entries.append(
                    _manifest_entry(
                        listing,
                        checksum="",
                        byte_count=len(response.content),
                        download_status="SKIPPED_BY_BYTE_LIMIT",
                        retry_status="BOUNDED_CLIENT_COMPLETE",
                        parser_status="NOT_PERSISTED",
                        isin=None,
                    )
                )
                continue
            isin = _validate_downloaded_xbrl(response.content, name, symbol)
            try:
                with destination.open("xb") as stream:
                    stream.write(response.content)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                already_present.append(name)
                checksums[name] = _sha256(_read_bounded(destination))
                continue
            downloaded.append(name)
            downloaded_bytes += len(response.content)
            checksum = _sha256(response.content)
            checksums[name] = checksum
            manifest_entries.append(
                _manifest_entry(
                    listing,
                    checksum=checksum,
                    byte_count=len(response.content),
                    download_status="DOWNLOADED",
                    retry_status="BOUNDED_CLIENT_COMPLETE",
                    parser_status="XBRL_ROOT_AND_IDENTITY_VALIDATED",
                    isin=isin,
                )
            )
    finally:
        if owned_client:
            http.close()
    manifest = {
        "manifest_version": "1",
        "adapter": f"{ADAPTER_CODE} v{ADAPTER_VERSION}",
        "symbol": symbol,
        "scope": normalized_scope or "ALL",
        "listing_file": listing_path.name,
        "listing_sha256": _sha256(listing_content),
        "artifacts": sorted(manifest_entries, key=lambda item: str(item["local_filename"])),
        "limits": {"max_files": max_files, "max_bytes": max_bytes},
    }
    manifest_content = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if not manifest_path.exists() or manifest_path.read_bytes() != manifest_content:
        temporary = manifest_path.with_suffix(".json.partial")
        temporary.write_bytes(manifest_content)
        os.replace(temporary, manifest_path)
    return DownloadReport(
        listing_file=listing_path.name,
        listing_rows=len(listings),
        downloaded=tuple(downloaded),
        already_present=tuple(already_present),
        skipped_by_limit=tuple(skipped_by_limit),
        downloaded_bytes=downloaded_bytes,
        manifest_file=MANIFEST_FILE_NAME,
        manifest_sha256=_sha256(manifest_content),
        checksums=checksums,
    )


def _manifest_entry(
    listing: ListingRow,
    *,
    checksum: str,
    byte_count: int,
    download_status: str,
    retry_status: str,
    parser_status: str,
    isin: str | None,
) -> dict[str, object]:
    return {
        "symbol": listing.symbol,
        "isin": isin,
        "scope": listing.scope,
        "filing_broadcast_at": listing.broadcast_at.isoformat(),
        "source_url": listing.xbrl_url,
        "local_filename": listing.xbrl_file_name,
        "sha256": checksum,
        "bytes": byte_count,
        "download_status": download_status,
        "retry_status": retry_status,
        "parser_status": parser_status,
    }


def _tag_parts(tag: str) -> tuple[str, str]:
    if tag.startswith("{") and "}" in tag:
        namespace, local = tag[1:].split("}", 1)
        return namespace, local
    return "", tag


def _identity_facts(root: ElementTree.Element) -> dict[str, str]:
    return {
        _tag_parts(item.tag)[1]: (item.text or "").strip()
        for item in root
        if item.attrib.get("contextRef")
        and _tag_parts(item.tag)[1]
        in {"Symbol", "ISIN", "NatureOfReportStandaloneConsolidated"}
    }


def _validate_downloaded_xbrl(content: bytes, name: str, symbol: str) -> str:
    lowered = content.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ArtifactValidationError("DTD/entity declarations are not permitted in XBRL artifacts")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise ArtifactValidationError(f"Downloaded XBRL is malformed: {name}") from exc
    if _tag_parts(root.tag) != (XBRLI, "xbrl"):
        raise ArtifactValidationError(f"Downloaded artifact is not XBRL: {name}")
    identity = _identity_facts(root)
    if identity.get("Symbol", "").upper() != symbol:
        raise ArtifactValidationError(
            f"Downloaded XBRL symbol does not match the listing: {name}"
        )
    isin = identity.get("ISIN", "").upper()
    if len(isin) != 12 or not isin.isalnum():
        raise ArtifactValidationError(f"Downloaded XBRL ISIN is malformed: {name}")
    return isin


def _contexts(root: ElementTree.Element) -> dict[str, XbrlContext]:
    output: dict[str, XbrlContext] = {}
    for element in root.findall(f"{{{XBRLI}}}context"):
        context_id = element.attrib.get("id", "").strip()
        period = element.find(f"{{{XBRLI}}}period")
        if not context_id or period is None:
            raise ArtifactValidationError("XBRL context is missing its id or period")
        instant_text = period.findtext(f"{{{XBRLI}}}instant")
        start_text = period.findtext(f"{{{XBRLI}}}startDate")
        end_text = period.findtext(f"{{{XBRLI}}}endDate")
        try:
            if instant_text:
                start = None
                end = date.fromisoformat(instant_text)
                instant = True
            elif start_text and end_text:
                start = date.fromisoformat(start_text)
                end = date.fromisoformat(end_text)
                instant = False
            else:
                raise ValueError
        except ValueError as exc:
            raise ArtifactValidationError(f"XBRL context {context_id} has an invalid period") from exc
        dimensions = tuple(
            (member.attrib.get("dimension", ""), (member.text or "").strip())
            for member in element.findall(f".//{{{XBRLDI}}}explicitMember")
        )
        if context_id in output:
            raise ArtifactValidationError(f"Duplicate XBRL context id: {context_id}")
        output[context_id] = XbrlContext(context_id, start, end, instant, dimensions)
    if not output:
        raise ArtifactValidationError("The XBRL instance contains no contexts")
    return output


def _units(root: ElementTree.Element) -> dict[str, str]:
    output: dict[str, str] = {}
    for element in root.findall(f"{{{XBRLI}}}unit"):
        unit_id = element.attrib.get("id", "").strip()
        measures = [(item.text or "").strip() for item in element.findall(f".//{{{XBRLI}}}measure")]
        if unit_id and measures:
            output[unit_id] = " / ".join(measures)
    return output


def _quarter(period_end: date) -> tuple[int, int]:
    if period_end.month == 6:
        return period_end.year + 1, 1
    if period_end.month == 9:
        return period_end.year + 1, 2
    if period_end.month == 12:
        return period_end.year + 1, 3
    if period_end.month == 3:
        return period_end.year, 4
    raise ArtifactValidationError("The listing period is not a supported Indian fiscal quarter end")


def _unit_code(unit_ref: str) -> str:
    aliases = {"INR": "INR", "PURE": "PURE", "INRPERSHARE": "INR_PER_SHARE"}
    return aliases.get(unit_ref.replace("_", "").upper(), unit_ref.upper()[:32])


def _value_nature(context: XbrlContext) -> str:
    if context.instant:
        return "INSTANT"
    assert context.start is not None
    days = (context.end - context.start).days + 1
    if 70 <= days <= 100:
        return "QUARTERLY"
    if 340 <= days <= 380:
        return "ANNUAL"
    return "YTD"


NORMALIZED_HEADERS = (
    "Symbol", "Series", "ISIN", "Source Filing ID", "Supersedes Filing ID",
    "Filing Type", "Reporting Frequency", "Period Start", "Period End",
    "Fiscal Year", "Fiscal Quarter", "Scope", "Audit Status",
    "Submission Timestamp", "Concept", "Value", "Unit", "Scale", "Fact Kind",
    "Value Nature", "Fact Period Start", "Fact Period End", "Source Namespace",
    "Source QName", "Source Context ID", "Source Unit", "Source Decimals",
    "Source Locator", "Source Artifact SHA256",
)


def _parse_instance(
    content: bytes,
    listing: ListingRow,
    file_name: str,
    raw_sha256: str,
    expected_isin: str | None,
) -> tuple[list[dict[str, object]], int, set[str]]:
    lowered = content.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ArtifactValidationError("DTD/entity declarations are not permitted in XBRL artifacts")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise ArtifactValidationError("Malformed XBRL XML") from exc
    if _tag_parts(root.tag) != (XBRLI, "xbrl"):
        raise ArtifactValidationError("The filing is not an XBRL instance document")
    contexts = _contexts(root)
    units = _units(root)
    primary = contexts.get("OneD")
    if (
        primary is None or primary.instant or primary.dimensions
        or primary.end != listing.period_end or primary.start is None
        or not 70 <= (primary.end - primary.start).days + 1 <= 100
    ):
        raise ArtifactValidationError("The filing does not contain the verified OneD quarterly context")
    selected_contexts = {
        key: item for key, item in contexts.items()
        if key in {"OneD", "FourD", "OneI"}
        and not item.dimensions and item.end == listing.period_end
    }
    facts = [element for element in root if element.attrib.get("contextRef") in selected_contexts]
    fact_namespaces = {
        _tag_parts(element.tag)[0]
        for element in facts
        if element.attrib.get("unitRef")
    }
    unsupported = sorted(fact_namespaces - SUPPORTED_TAXONOMY_NAMESPACES)
    if unsupported:
        raise ArtifactValidationError(
            "Unsupported XBRL taxonomy namespace: " + ", ".join(unsupported)
        )
    identity = {
        _tag_parts(item.tag)[1]: (item.text or "").strip()
        for item in facts
        if _tag_parts(item.tag)[1] in {"Symbol", "ISIN", "NatureOfReportStandaloneConsolidated"}
    }
    if identity.get("Symbol", "").upper() != listing.symbol:
        raise ArtifactValidationError("XBRL symbol does not match the listing row")
    isin = identity.get("ISIN", "").upper()
    if len(isin) != 12 or not isin.isalnum():
        raise ArtifactValidationError("XBRL ISIN is malformed")
    if expected_isin and isin != expected_isin.strip().upper():
        raise ArtifactValidationError("XBRL ISIN does not match the official security master")
    if identity.get("NatureOfReportStandaloneConsolidated", "").upper() != listing.scope:
        raise ArtifactValidationError("XBRL scope does not match the listing row")
    fiscal_year, fiscal_quarter = _quarter(listing.period_end)
    rows: list[dict[str, object]] = []
    seen: dict[tuple[str, date, date], Decimal] = {}
    for element in facts:
        unit_ref = element.attrib.get("unitRef")
        if not unit_ref:
            continue
        namespace, local = _tag_parts(element.tag)
        if namespace not in SUPPORTED_TAXONOMY_NAMESPACES:
            continue
        context = selected_contexts[element.attrib["contextRef"]]
        text = (element.text or "").strip().replace(",", "")
        try:
            value = Decimal(text)
        except InvalidOperation as exc:
            raise ArtifactValidationError(f"Numeric XBRL fact {local} is malformed") from exc
        fact_start = context.end if context.instant else context.start
        assert fact_start is not None
        fact_identity = (local.casefold(), fact_start, context.end)
        if fact_identity in seen:
            if seen[fact_identity] != value:
                raise ArtifactValidationError(f"Conflicting duplicate XBRL fact: {local}")
            continue
        seen[fact_identity] = value
        rows.append(
            {
                "Symbol": listing.symbol,
                "Series": "EQ",
                "ISIN": isin,
                "Source Filing ID": listing.source_filing_id,
                "Supersedes Filing ID": "",
                "Filing Type": "FINANCIAL_RESULTS",
                "Reporting Frequency": "QUARTERLY",
                "Period Start": primary.start,
                "Period End": listing.period_end,
                "Fiscal Year": fiscal_year,
                "Fiscal Quarter": fiscal_quarter,
                "Scope": listing.scope,
                "Audit Status": listing.audit_status,
                "Submission Timestamp": listing.broadcast_at.isoformat(),
                "Concept": local,
                "Value": str(value),
                "Unit": _unit_code(unit_ref),
                "Scale": 0,
                "Fact Kind": "INSTANT" if context.instant else "DURATION",
                "Value Nature": _value_nature(context),
                "Fact Period Start": fact_start,
                "Fact Period End": context.end,
                "Source Namespace": namespace,
                "Source QName": element.tag,
                "Source Context ID": context.context_id,
                "Source Unit": units.get(unit_ref, unit_ref),
                "Source Decimals": element.attrib.get("decimals", ""),
                "Source Locator": listing.xbrl_url,
                "Source Artifact SHA256": raw_sha256,
            }
        )
    if not rows:
        raise ArtifactValidationError("The linked XBRL filing has no eligible numeric facts")
    return rows, len(contexts), fact_namespaces


def prepare_fundamentals_ixbrl(
    value: str | Path,
    *,
    symbol: str,
    expected_isin: str | None = None,
    store: NseArtifactStore | None = None,
) -> BundleReport:
    symbol = _validate_phase_scope(symbol)
    artifact_store = store or NseArtifactStore()
    directory, listing_path = _resolve_bundle(value, artifact_store)
    listing_content = _read_bounded(listing_path)
    listings = _listing_rows(listing_content, symbol)
    by_file: dict[str, list[ListingRow]] = {}
    for item in listings:
        by_file.setdefault(item.xbrl_file_name, []).append(item)
    xml_paths = sorted(directory.glob("*.xml"))
    local_names = {item.name for item in xml_paths}
    missing = tuple(sorted(name for name in by_file if name not in local_names))
    rejected: list[str] = []
    normalized_rows: list[dict[str, object]] = []
    raw_checksums = {listing_path.name: _sha256(listing_content)}
    context_count = 0
    taxonomy_namespaces: set[str] = set()
    unsupported_taxonomy_namespaces: set[str] = set()
    matched = 0
    linked_listings: list[ListingRow] = []
    for path in xml_paths:
        candidates = by_file.get(path.name, [])
        if len(candidates) != 1:
            rejected.append(f"{path.name}: {'UNLINKED' if not candidates else 'AMBIGUOUS_LISTING_LINK'}")
            continue
        content = _read_bounded(path)
        checksum = _sha256(content)
        raw_checksums[path.name] = checksum
        try:
            rows, contexts, namespaces = _parse_instance(
                content,
                candidates[0],
                path.name,
                checksum,
                expected_isin,
            )
        except ArtifactValidationError as exc:
            prefix = "Unsupported XBRL taxonomy namespace: "
            if str(exc).startswith(prefix):
                unsupported_taxonomy_namespaces.update(
                    item.strip() for item in str(exc)[len(prefix):].split(",")
                )
            rejected.append(f"{path.name}: {exc}")
            continue
        normalized_rows.extend(rows)
        context_count += contexts
        taxonomy_namespaces.update(namespaces)
        matched += 1
        linked_listings.append(candidates[0])
    if not normalized_rows:
        raise ArtifactValidationError("No valid linked XBRL filings were discovered")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=NORMALIZED_HEADERS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(normalized_rows)
    encoded = stream.getvalue().encode("utf-8")
    listing_checksum = raw_checksums[listing_path.name]
    artifact = ArtifactBytes(
        file_name=f"{symbol}_xbrl_bundle_{max(item.broadcast_at for item in linked_listings):%Y%m%d}.csv",
        content=encoded,
        source_locator=f"local-inbox-bundle:{directory.name}:listing-sha256:{listing_checksum}",
    )
    mapped = sum(1 for row in normalized_rows if normalize_concept(str(row["Concept"])) is not None)
    return BundleReport(
        artifact=artifact,
        listing_file=listing_path.name,
        local_artifacts=tuple(path.name for path in xml_paths),
        listing_rows=len(listings),
        matched_filings=matched,
        rejected_filings=tuple(rejected),
        missing_listing_artifacts=missing,
        context_count=context_count,
        fact_count=len(normalized_rows),
        mapped_fact_count=mapped,
        unmapped_fact_count=len(normalized_rows) - mapped,
        earliest_available_at=min((item.broadcast_at for item in linked_listings), default=None),
        latest_available_at=max((item.broadcast_at for item in linked_listings), default=None),
        scopes=tuple(sorted({item.scope for item in linked_listings})),
        audit_statuses=tuple(sorted({item.audit_status for item in linked_listings})),
        supported_taxonomy_families=tuple(
            sorted(SUPPORTED_TAXONOMY_FAMILIES[item] for item in taxonomy_namespaces)
        ),
        unsupported_taxonomy_namespaces=tuple(
            sorted(unsupported_taxonomy_namespaces)
        ),
        raw_checksums=raw_checksums,
    )
