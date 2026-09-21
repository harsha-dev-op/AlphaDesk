from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.fundamentals.definitions import normalize_concept
from app.fundamentals.service import FundamentalIntelligenceService
from app.ingestion.nse.artifacts import ArtifactValidationError, NseArtifactStore
from app.ingestion.nse.client import OfficialHttpClient
from app.ingestion.nse.ixbrl import download_fundamentals_ixbrl, prepare_fundamentals_ixbrl
from app.ingestion.nse.parsers import parse_financial_results
from app.ingestion.nse.service import NseIngestionService
from app.models import FundamentalFact, FundamentalFiling, Security
from app.services.data_sources import DataSourceCoverageService


LISTING_HEADERS = (
    "SYMBOL", "COMPANY NAME", "QUARTER END DATE", "TYPE OF SUBMISSION",
    "AUDITED / UNAUDITED", "CONSOLIDATED / STANDALONE", "DETAILS", "XBRL",
    "BROADCAST DATE/TIME", "REVISED DATE/TIME", "REVISION REMARKS",
    "EXCHANGE DISSEMINATION TIME", "TIME TAKEN",
)


def _xml(
    *,
    symbol: str = "RELIANCE",
    isin: str = "INE002A01018",
    scope: str = "Consolidated",
    end: str = "2026-06-30",
    duplicate: str = "",
    namespace: str = "http://www.sebi.gov.in/xbrl/2026-01-31/in-capmkt",
) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
 xmlns:in-capmkt="{namespace}"
 xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
 <xbrli:context id="OneD"><xbrli:entity><xbrli:identifier scheme="NSE">RELIANCE</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2026-04-01</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:context id="FourD"><xbrli:entity><xbrli:identifier scheme="NSE">RELIANCE</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2026-04-01</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:context id="OneI"><xbrli:entity><xbrli:identifier scheme="NSE">RELIANCE</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>{end}</xbrli:instant></xbrli:period></xbrli:context>
 <xbrli:unit id="INR"><xbrli:measure>iso4217:INR</xbrli:measure></xbrli:unit>
 <xbrli:unit id="INRPerShare"><xbrli:measure>in-capmkt:INRPerShare</xbrli:measure></xbrli:unit>
 <in-capmkt:Symbol contextRef="OneD">{symbol}</in-capmkt:Symbol>
 <in-capmkt:ISIN contextRef="OneD">{isin}</in-capmkt:ISIN>
 <in-capmkt:NatureOfReportStandaloneConsolidated contextRef="OneD">{scope}</in-capmkt:NatureOfReportStandaloneConsolidated>
 <in-capmkt:RevenueFromOperations contextRef="OneD" unitRef="INR" decimals="-7">3118500000000</in-capmkt:RevenueFromOperations>
 {duplicate}
 <in-capmkt:ProfitLossForPeriod contextRef="OneD" unitRef="INR" decimals="-7">231960000000</in-capmkt:ProfitLossForPeriod>
 <in-capmkt:BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations contextRef="OneD" unitRef="INRPerShare" decimals="2">15.49</in-capmkt:BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations>
 <in-capmkt:Equity contextRef="OneI" unitRef="INR" decimals="-7">10858660000000</in-capmkt:Equity>
 <in-capmkt:RevenueFromOperations contextRef="FourD" unitRef="INR" decimals="-7">3118500000000</in-capmkt:RevenueFromOperations>
 <in-capmkt:UnknownRelianceFact contextRef="OneD" unitRef="INR" decimals="0">42</in-capmkt:UnknownRelianceFact>
</xbrli:xbrl>'''


def _bundle(
    tmp_path: Path,
    *,
    symbol: str = "RELIANCE",
    isin: str = "INE002A01018",
    scope: str = "Consolidated",
    audit: str = "Un-Audited",
    xml: str | None = None,
    host: str = "nsearchives.nseindia.com",
) -> tuple[NseArtifactStore, Path]:
    store = NseArtifactStore(root=tmp_path / "nse")
    directory = store.inbox / "phase13"
    directory.mkdir(parents=True)
    file_name = "INTEGRATED_FILING_INDAS_1695741_17072026075004_WEB.xml"
    (directory / file_name).write_text(
        xml or _xml(symbol=symbol, isin=isin, scope=scope), encoding="utf-8"
    )
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=LISTING_HEADERS, lineterminator="\n")
    writer.writeheader()
    writer.writerow({
        "SYMBOL": symbol, "COMPANY NAME": f"{symbol} Limited",
        "QUARTER END DATE": "30-JUN-2026", "TYPE OF SUBMISSION": "Original",
        "AUDITED / UNAUDITED": audit, "CONSOLIDATED / STANDALONE": scope,
        "DETAILS": "https://nsearchives.nseindia.com/corporate/ixbrl/example.html",
        "XBRL": f"https://{host}/corporate/xbrl/{file_name}",
        "BROADCAST DATE/TIME": "17-Jul-2026 19:50:03",
        "REVISED DATE/TIME": "", "REVISION REMARKS": "",
        "EXCHANGE DISSEMINATION TIME": "17-Jul-2026 19:50:04", "TIME TAKEN": "00:00:01",
    })
    (directory / "listing.csv").write_text(stream.getvalue(), encoding="utf-8")
    return store, directory


def _security(symbol: str = "RELIANCE", isin: str = "INE002A01018") -> Security:
    return Security(
        exchange="NSE", symbol=symbol, trading_symbol=f"{symbol}-EQ", series="EQ",
        company_name=f"{symbol} Limited", isin=isin,
        security_type="EQUITY", currency="INR", is_active=True,
        data_origin="OFFICIAL_NSE_PUBLIC",
    )


def test_real_nse_xbrl_shape_extracts_numeric_facts_contexts_and_units(tmp_path):
    store, _ = _bundle(tmp_path)
    result = prepare_fundamentals_ixbrl("phase13", symbol="RELIANCE", store=store)
    parsed = parse_financial_results(result.artifact, source_date=result.source_date)
    assert result.matched_filings == 1 and result.context_count == 3
    assert result.fact_count == 5 and parsed.rejected_row_count == 0
    revenue = next(row for row in parsed.rows if row.source_concept == "RevenueFromOperations" and row.value_nature == "QUARTERLY")
    assert revenue.value == 3118500000000
    assert revenue.source_unit == "iso4217:INR"
    assert revenue.source_decimals == "-7"
    assert revenue.source_context_id == "OneD"


def test_listing_linkage_preserves_consolidated_audit_and_broadcast_time(tmp_path):
    store, _ = _bundle(tmp_path, audit="Audited")
    result = prepare_fundamentals_ixbrl("phase13", symbol="RELIANCE", store=store)
    row = parse_financial_results(result.artifact, source_date=result.source_date).rows[0]
    assert row.scope == "CONSOLIDATED" and row.audit_status == "AUDITED"
    assert row.submission_at == datetime(2026, 7, 17, 14, 20, 3, tzinfo=UTC)
    assert row.source_filing_id == "NSE_XBRL_1695741"


@pytest.mark.parametrize(
    ("source", "normalized"),
    [
        ("RevenueFromOperations", "REVENUE"),
        ("ProfitLossForPeriod", "PROFIT_AFTER_TAX"),
        ("BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations", "EPS_BASIC"),
        ("Equity", "TOTAL_EQUITY"),
        ("UnknownRelianceFact", None),
    ],
)
def test_verified_reliance_concept_mappings_are_exact(source, normalized):
    assert normalize_concept(source) == normalized


def test_unknown_concept_and_raw_qname_remain_auditable(tmp_path):
    store, _ = _bundle(tmp_path)
    result = prepare_fundamentals_ixbrl("phase13", symbol="RELIANCE", store=store)
    row = next(item for item in parse_financial_results(result.artifact).rows if item.source_concept == "UnknownRelianceFact")
    assert normalize_concept(row.source_concept) is None
    assert row.source_namespace == "http://www.sebi.gov.in/xbrl/2026-01-31/in-capmkt"
    assert row.source_qname and row.source_qname.endswith("}UnknownRelianceFact")
    assert len(row.source_artifact_sha256 or "") == 64


def test_exact_duplicate_fact_is_collapsed_deterministically(tmp_path):
    duplicate = '<in-capmkt:RevenueFromOperations contextRef="OneD" unitRef="INR" decimals="-7">3118500000000</in-capmkt:RevenueFromOperations>'
    store, _ = _bundle(tmp_path, xml=_xml(duplicate=duplicate))
    result = prepare_fundamentals_ixbrl("phase13", symbol="RELIANCE", store=store)
    assert result.fact_count == 5


def test_conflicting_duplicate_fact_rejects_filing(tmp_path):
    duplicate = '<in-capmkt:RevenueFromOperations contextRef="OneD" unitRef="INR" decimals="-7">1</in-capmkt:RevenueFromOperations>'
    store, _ = _bundle(tmp_path, xml=_xml(duplicate=duplicate))
    with pytest.raises(ArtifactValidationError, match="No valid linked"):
        prepare_fundamentals_ixbrl("phase13", symbol="RELIANCE", store=store)


def test_scope_mismatch_rejects_filing(tmp_path):
    store, _ = _bundle(tmp_path, scope="Standalone", xml=_xml(scope="Consolidated"))
    with pytest.raises(ArtifactValidationError, match="No valid linked"):
        prepare_fundamentals_ixbrl("phase13", symbol="RELIANCE", store=store)


def test_non_official_listing_host_is_rejected(tmp_path):
    store, _ = _bundle(tmp_path, host="example.com")
    with pytest.raises(ArtifactValidationError, match="non-official"):
        prepare_fundamentals_ixbrl("phase13", symbol="RELIANCE", store=store)


@pytest.mark.parametrize(
    ("symbol", "isin"),
    [("RELIANCE", "INE002A01018"), ("TCS", "INE467B01029"), ("INFY", "INE009A01021")],
)
def test_generic_ind_as_adapter_accepts_verified_non_financial_identity(
    tmp_path, symbol, isin
):
    store, _ = _bundle(tmp_path, symbol=symbol, isin=isin)
    result = prepare_fundamentals_ixbrl(
        "phase13", symbol=symbol, expected_isin=isin, store=store
    )
    assert result.matched_filings == 1
    assert result.supported_taxonomy_families == ("SEBI_IN_CAPMKT_2026",)


@pytest.mark.parametrize("symbol", ["HDFCBANK", "ICICIBANK"])
def test_phase13d_excludes_financial_sector_symbols(tmp_path, symbol):
    store, _ = _bundle(tmp_path, symbol=symbol)
    with pytest.raises(ArtifactValidationError, match="excludes financial-sector"):
        prepare_fundamentals_ixbrl("phase13", symbol=symbol, store=store)


def test_expected_security_master_isin_mismatch_rejects_filing(tmp_path):
    store, _ = _bundle(tmp_path, symbol="TCS", isin="INE467B01029")
    with pytest.raises(ArtifactValidationError, match="No valid linked"):
        prepare_fundamentals_ixbrl(
            "phase13",
            symbol="TCS",
            expected_isin="INE009A01021",
            store=store,
        )


def test_unsupported_taxonomy_family_is_rejected(tmp_path):
    store, _ = _bundle(
        tmp_path,
        xml=_xml(namespace="http://example.com/unsupported-taxonomy"),
    )
    with pytest.raises(ArtifactValidationError, match="No valid linked"):
        prepare_fundamentals_ixbrl("phase13", symbol="RELIANCE", store=store)


def test_dtd_or_entity_declarations_are_rejected(tmp_path):
    store, _ = _bundle(tmp_path, xml='<!DOCTYPE x [<!ENTITY x "1">]><x/>')
    with pytest.raises(ArtifactValidationError, match="No valid linked"):
        prepare_fundamentals_ixbrl("phase13", symbol="RELIANCE", store=store)


def test_optional_download_fetches_exact_listing_url_without_overwrite(tmp_path):
    store, directory = _bundle(tmp_path)
    xml_path = next(directory.glob("*.xml"))
    expected = xml_path.read_bytes()
    xml_path.unlink()
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, headers={"Content-Type": "application/xml"}, content=expected)

    with OfficialHttpClient(
        transport=httpx.MockTransport(handler), minimum_spacing_seconds=0
    ) as client:
        first = download_fundamentals_ixbrl(
            "phase13", symbol="RELIANCE", store=store, client=client
        )
        second = download_fundamentals_ixbrl(
            "phase13", symbol="RELIANCE", store=store, client=client
        )

    assert first.downloaded == (xml_path.name,)
    assert second.already_present == (xml_path.name,)
    assert len(requests) == 1
    assert requests[0].endswith(xml_path.name)
    assert xml_path.read_bytes() == expected
    assert first.manifest_sha256 == second.manifest_sha256
    assert (directory / "fundamentals-xbrl-manifest.json").is_file()


def test_acquisition_manifest_detects_existing_checksum_mismatch(tmp_path):
    store, directory = _bundle(tmp_path)
    xml_path = next(directory.glob("*.xml"))
    download_fundamentals_ixbrl("phase13", symbol="RELIANCE", store=store)
    xml_path.write_bytes(xml_path.read_bytes() + b" ")
    with pytest.raises(ArtifactValidationError, match="does not match its manifest"):
        download_fundamentals_ixbrl("phase13", symbol="RELIANCE", store=store)


def test_acquisition_total_byte_limit_skips_without_persisting(tmp_path):
    store, directory = _bundle(tmp_path)
    xml_path = next(directory.glob("*.xml"))
    expected = xml_path.read_bytes()
    xml_path.unlink()

    with OfficialHttpClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"Content-Type": "application/xml"},
                content=expected,
            )
        ),
        minimum_spacing_seconds=0,
    ) as client:
        result = download_fundamentals_ixbrl(
            "phase13",
            symbol="RELIANCE",
            max_bytes=len(expected) - 1,
            store=store,
            client=client,
        )

    assert result.downloaded == ()
    assert result.skipped_by_limit == (xml_path.name,)
    assert not xml_path.exists()


def test_import_is_idempotent_and_preserves_fact_provenance(db, tmp_path):
    db.add(_security()); db.commit()
    store, _ = _bundle(tmp_path)
    service = NseIngestionService(db, store=store)
    first = service.import_fundamentals_ixbrl("phase13", symbol="RELIANCE")
    second = service.import_fundamentals_ixbrl("phase13", symbol="RELIANCE")
    assert first["ingestion"]["inserted"] == 5
    assert second["ingestion"]["repeated_artifact"] is True
    assert db.scalar(select(func.count()).select_from(FundamentalFiling)) == 1
    assert db.scalar(select(func.count()).select_from(FundamentalFact)) == 5
    fact = db.scalar(select(FundamentalFact).where(FundamentalFact.source_concept == "RevenueFromOperations"))
    assert fact is not None and fact.fact_metadata["source_context_id"] == "OneD"


def test_generic_tcs_import_uses_security_master_identity_and_data_health(db, tmp_path):
    db.add(_security("TCS", "INE467B01029")); db.commit()
    store, _ = _bundle(tmp_path, symbol="TCS", isin="INE467B01029")
    result = NseIngestionService(db, store=store).import_fundamentals_ixbrl(
        "phase13", symbol="TCS"
    )
    fundamentals = next(
        item
        for item in DataSourceCoverageService(db).coverage().activation_datasets
        if item.code == "FUNDAMENTALS"
    )
    assert result["ingestion"]["inserted"] == 5
    assert fundamentals.metrics["mapped_facts"] == 4
    assert fundamentals.metrics["unmapped_facts"] == 1
    assert fundamentals.metrics["supported_taxonomy_families"] == "SEBI_IN_CAPMKT_2026"
    assert fundamentals.metrics["unsupported_taxonomy_family_count"] == 0


def test_generic_import_rejects_security_master_isin_mismatch(db, tmp_path):
    db.add(_security("TCS", "INE467B01029")); db.commit()
    store, _ = _bundle(tmp_path, symbol="TCS", isin="INE009A01021")
    with pytest.raises(ArtifactValidationError, match="No valid linked"):
        NseIngestionService(db, store=store).import_fundamentals_ixbrl(
            "phase13", symbol="TCS", dry_run=True
        )


def test_imported_filing_is_point_in_time_and_activates_latest_metrics(db, tmp_path):
    db.add(_security()); db.commit()
    store, _ = _bundle(tmp_path)
    NseIngestionService(db, store=store).import_fundamentals_ixbrl("phase13", symbol="RELIANCE")
    service = FundamentalIntelligenceService(db)
    before = service.fundamentals("RELIANCE", as_of=datetime(2026, 7, 17, 14, 20, 2, tzinfo=UTC))
    after = service.fundamentals("RELIANCE", as_of=datetime(2026, 7, 17, 14, 20, 3, tzinfo=UTC))
    metrics = {item.code: item for item in service.metrics("RELIANCE", as_of=datetime(2026, 7, 18, tzinfo=UTC)).metrics}
    assert before.status == "UNAVAILABLE" and after.status == "READY"
    assert metrics["REVENUE_LATEST_QUARTER"].value == 3118500000000
    assert metrics["PAT_LATEST_QUARTER"].value == 231960000000
    assert metrics["EPS_LATEST_QUARTER"].value == Decimal("15.49000000")
    assert metrics["REVENUE_TTM"].status == "UNAVAILABLE"
