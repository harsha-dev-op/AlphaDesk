from __future__ import annotations

import io
import zipfile
from datetime import date

import httpx
import pytest

from app.ingestion.nse import artifacts as artifact_module
from app.ingestion.nse.artifacts import (
    ArtifactBytes,
    ArtifactSchemaDriftError,
    ArtifactValidationError,
    csv_records,
)
from app.ingestion.nse.client import (
    OfficialHttpClient,
    OfficialSourceAccessDenied,
    OfficialSourceError,
    OfficialSourceValidationError,
    validate_official_url,
)
from app.ingestion.nse.definitions import ArtifactType, eod_artifact_type, public_artifact_url
from app.ingestion.nse.parsers import (
    parse_bhavcopy,
    parse_constituents,
    parse_corporate_actions,
    parse_index_history,
    parse_security_master,
)


def _artifact(name: str, text: str) -> ArtifactBytes:
    return ArtifactBytes(name, text.encode(), f"fixture:{name}")


def _zip(name: str, content: bytes, *, member: str = "data.csv") -> ArtifactBytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, content)
    return ArtifactBytes(name, stream.getvalue(), f"fixture:{name}")


def _client(handler, **kwargs) -> OfficialHttpClient:
    return OfficialHttpClient(
        transport=httpx.MockTransport(handler),
        minimum_spacing_seconds=0,
        sleep=kwargs.pop("sleep", lambda _: None),
        **kwargs,
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://www.nseindia.com/all-reports",
        "https://nsearchives.nseindia.com/content/cm/file.csv.zip",
        "https://www.niftyindices.com/indices/equity",
    ],
)
def test_official_url_allowlist_accepts_only_declared_domains(url):
    validate_official_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://nseindia.com/file.csv",
        "https://nseindia.com.evil.example/file.csv",
        "https://example.com/file.csv",
        "https://user:secret@nseindia.com/file.csv",
    ],
)
def test_official_url_allowlist_rejects_unsafe_urls(url):
    with pytest.raises(OfficialSourceValidationError):
        validate_official_url(url)


def test_http_timeout_retries_are_bounded_and_sanitized():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("provider detail must not escape", request=request)

    with _client(handler, max_attempts=3) as client:
        with pytest.raises(OfficialSourceError, match="timed out or failed") as exc_info:
            client.fetch("https://nsearchives.nseindia.com/file.csv")
    assert attempts == 3
    assert "provider detail" not in str(exc_info.value)


def test_http_429_honors_bounded_retry_after_then_succeeds():
    attempts = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "120"})
        return httpx.Response(200, headers={"Content-Type": "text/csv"}, content=b"a\n1\n")

    with _client(handler, max_attempts=2, sleep=sleeps.append) as client:
        result = client.fetch("https://nsearchives.nseindia.com/file.csv")
    assert result.content == b"a\n1\n"
    assert sleeps == [60.0]


def test_http_403_is_not_retried_or_bypassed():
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(403)

    with _client(handler, max_attempts=5) as client:
        with pytest.raises(OfficialSourceAccessDenied, match="local artifact import"):
            client.fetch("https://nsearchives.nseindia.com/file.csv")
    assert attempts == 1


def test_http_5xx_retries_then_succeeds():
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503)
        return httpx.Response(200, headers={"Content-Type": "text/csv"}, content=b"a\n1\n")

    with _client(handler, max_attempts=3) as client:
        assert client.fetch("https://nsearchives.nseindia.com/file.csv").content
    assert attempts == 3


@pytest.mark.parametrize(
    ("headers", "content", "message"),
    [
        ({"Content-Type": "text/html"}, b"x", "content type"),
        ({"Content-Type": "text/csv", "Content-Length": "9"}, b"a\n1\n", "size ceiling"),
        ({"Content-Type": "text/csv", "Content-Length": "invalid"}, b"a\n1\n", "content length"),
    ],
)
def test_http_response_validation(headers, content, message):
    with _client(lambda _: httpx.Response(200, headers=headers, content=content), maximum_bytes=4) as client:
        with pytest.raises(OfficialSourceValidationError, match=message):
            client.fetch("https://nsearchives.nseindia.com/file.csv")


def test_http_streamed_body_size_ceiling():
    with _client(
        lambda _: httpx.Response(200, headers={"Content-Type": "text/csv"}, content=b"12345"),
        maximum_bytes=4,
    ) as client:
        with pytest.raises(OfficialSourceValidationError, match="size ceiling"):
            client.fetch("https://nsearchives.nseindia.com/file.csv")


def test_http_json_post_uses_the_official_contract_without_changing_safety_controls():
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["body"] = request.read().decode()
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=b"[]",
        )

    with _client(handler) as client:
        result = client.post_json(
            "https://www.niftyindices.com/BackPage/getHistoricaldatatabletoString",
            {"cinfo": "{'name':'NIFTY 200'}"},
        )
    assert result.content == b"[]"
    assert captured["method"] == "POST"
    assert '"cinfo"' in captured["body"]
    assert "NIFTY 200" in captured["body"]


def test_archive_and_csv_rejections_cover_malformed_traversal_and_rows():
    with pytest.raises(ArtifactValidationError, match="Malformed ZIP"):
        csv_records(ArtifactBytes("bad.zip", b"not-a-zip", "fixture:bad"))
    with pytest.raises(ArtifactValidationError, match="path is unsafe"):
        csv_records(_zip("traversal.zip", b"a\n1\n", member="../escape.csv"))
    with pytest.raises(ArtifactValidationError, match="Malformed CSV row"):
        csv_records(_artifact("bad.csv", "a,b\n1,2,3\n"))


def test_csv_reader_accepts_only_an_empty_trailing_official_column():
    headers, rows = csv_records(_artifact("legacy.csv", "A,B,\n1,2,\n"))
    assert headers == ["A", "B"]
    assert rows == [(2, {"A": "1", "B": "2"})]
    with pytest.raises(ArtifactValidationError, match="trailing CSV column"):
        csv_records(_artifact("bad-trailing.csv", "A,B,\n1,2,unexpected\n"))


def test_archive_decompression_ceiling_is_enforced(monkeypatch):
    monkeypatch.setattr(artifact_module, "MAX_DECOMPRESSED_BYTES", 8)
    with pytest.raises(ArtifactValidationError, match="decompression ceiling"):
        csv_records(_zip("large.zip", b"column\n123456789\n"))


def test_checksum_is_deterministic_and_content_sensitive():
    first = _artifact("one.csv", "a\n1\n")
    same = _artifact("two.csv", "a\n1\n")
    changed = _artifact("one.csv", "a\n2\n")
    assert first.sha256 == same.sha256
    assert first.sha256 != changed.sha256
    assert len(first.sha256) == 64


def test_security_master_official_schema_policy_and_identity_validation():
    parsed = parse_security_master(
        _artifact(
            "security.csv",
            "TckrSymb,SctySrs,FinInstrmNm,ISIN,DtOfListing,Normal Market Status\n"
            "ALPHA,EQ,Alpha Limited,INE000A01001,2020-01-02,ACTIVE\n"
            "ALPHA,EQ,Duplicate Symbol,INE000A01002,2020-01-02,ACTIVE\n"
            "BETA,EQ,Duplicate ISIN,INE000A01001,2020-01-02,ACTIVE\n"
            "GAMMA,BE,Unsupported Series,INE000A01003,2020-01-02,ACTIVE\n"
            "BAD SYMBOL,EQ,Bad Symbol,INVALID,2020-01-02,ACTIVE\n",
        )
    )
    assert [(row.symbol, row.series, row.isin) for row in parsed.rows] == [
        ("ALPHA", "EQ", "INE000A01001")
    ]
    assert parsed.rejected_row_count == 4
    assert {issue.code for issue in parsed.issues} == {
        "DUPLICATE_SYMBOL_SERIES",
        "DUPLICATE_ISIN_CONFLICT",
        "UNSUPPORTED_EQUITY_SERIES",
        "MALFORMED_SECURITY_IDENTIFIER",
    }


def test_bhavcopy_validates_date_ohlcv_volume_series_and_duplicates():
    header = "TradDt,Sgmt,TckrSymb,SctySrs,ISIN,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol,TtlTrfVal\n"
    parsed = parse_bhavcopy(
        _artifact(
            "prices.csv",
            header
            + "2026-09-11,CM,ALPHA,EQ,INE000A01001,100,110,90,105,12,1260\n"
            + "2026-09-11,CM,ALPHA,EQ,INE000A01001,100,110,90,105,12,1260\n"
            + "2026-09-11,CM,BETA,EQ,INE000A01002,100,90,95,96,1,96\n"
            + "2026-09-11,CM,GAMMA,EQ,INE000A01003,80,110,90,100,1,100\n"
            + "2026-09-11,CM,DELTA,EQ,INE000A01004,100,110,90,120,1,120\n"
            + "2026-09-11,CM,EPSILON,EQ,INE000A01005,100,110,90,100,-1,100\n"
            + "2026-09-11,CM,ZERO,EQ,INE000A01006,0,0,0,0,0,0\n"
            + "2026-09-11,CM,NULLS,EQ,INE000A01007,,110,90,100,1,100\n"
            + "2026-09-11,CM,SERIES,BE,INE000A01008,100,110,90,100,1,100\n"
            + "2026-09-10,CM,WRONGDATE,EQ,INE000A01009,100,110,90,100,1,100\n"
            + "2026-09-11,CM,RANGE,EQ,INE000A01010,100000000000000,100000000000001,99999999999999,100000000000000,1,100\n"
            + "2026-09-11,CM,SCALE,EQ,INE000A01011,100.00001,101,99,100,1,100\n",
        ),
        expected_date=date(2026, 9, 11),
    )
    assert len(parsed.rows) == 1
    assert parsed.rows[0].open == 100
    assert parsed.rows[0].volume == 12
    assert parsed.rejected_row_count == 11
    assert {issue.code for issue in parsed.issues} == {
        "DUPLICATE_ARTIFACT_ROW",
        "INVALID_OHLCV_ROW",
        "UNSUPPORTED_EQUITY_SERIES",
    }


def test_wrong_schema_and_artifact_date_are_explicit():
    with pytest.raises(ArtifactSchemaDriftError, match="missing required columns"):
        parse_security_master(_artifact("wrong.csv", "Something,Else\na,b\n"))
    parsed = parse_bhavcopy(
        _artifact(
            "date.csv",
            "TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\n"
            "2026-09-10,ALPHA,EQ,100,110,90,105,1\n",
        ),
        expected_date=date(2026, 9, 11),
    )
    assert parsed.rejected_row_count == 1
    assert "does not match" in parsed.issues[0].message


def test_duplicate_csv_headers_are_schema_drift():
    with pytest.raises(ArtifactSchemaDriftError, match="duplicate column"):
        csv_records(_artifact("duplicate-header.csv", "Symbol,symbol\nA,A\n"))


def test_constituent_parser_rejects_duplicate_and_unknown_series():
    parsed = parse_constituents(
        _artifact(
            "members.csv",
            "Company Name,Industry,Symbol,Series,ISIN Code\n"
            "Alpha,Finance,ALPHA,EQ,INE000A01001\n"
            "Alpha,Finance,ALPHA,EQ,INE000A01001\n"
            "Beta,Finance,BETA,BE,INE000A01002\n",
        )
    )
    assert len(parsed.rows) == 1
    assert {issue.code for issue in parsed.issues} == {
        "DUPLICATE_INDEX_MEMBER",
        "INVALID_CONSTITUENT",
    }


def test_legacy_bhavcopy_schema_and_official_transition_are_explicit():
    artifact_day = date(2021, 1, 4)
    parsed = parse_bhavcopy(
        _artifact(
            "cm04JAN2021bhav.csv",
            "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,ISIN,\n"
            "ALPHA,EQ,100,110,90,105,12,1260,04-JAN-2021,INE000A01001,\n",
        ),
        expected_date=artifact_day,
    )
    assert len(parsed.rows) == 1
    assert parsed.rows[0].volume == 12
    assert eod_artifact_type(date(2024, 7, 5)) == ArtifactType.LEGACY_EOD_BHAVCOPY
    assert eod_artifact_type(date(2024, 7, 8)) == ArtifactType.EOD_BHAVCOPY
    assert public_artifact_url(
        ArtifactType.LEGACY_EOD_BHAVCOPY, artifact_day
    ).endswith("/2021/JAN/cm04JAN2021bhav.csv.zip")


def test_index_history_parser_accepts_official_json_and_sorts_deterministically():
    parsed = parse_index_history(
        _artifact(
            "nifty200.json",
            '[{"INDEX_NAME":"NIFTY 200","HistoricalDate":"04 Jan 2021",'
            '"OPEN":"180.1","HIGH":"184.2","LOW":"179.5","CLOSE":"183.7"},'
            '{"INDEX_NAME":"Nifty 200","HistoricalDate":"01 Jan 2021",'
            '"OPEN":"175","HIGH":"180","LOW":"174","CLOSE":"179"}]',
        ),
        requested_start=date(2021, 1, 1),
        requested_end=date(2021, 12, 31),
    )
    assert [row.trading_date for row in parsed.rows] == [
        date(2021, 1, 1),
        date(2021, 1, 4),
    ]
    assert parsed.rows[0].close == 179
    assert parsed.rejected_row_count == 0


def test_index_history_parser_supports_csv_and_rejects_bad_identity_ohlc_and_duplicates():
    parsed = parse_index_history(
        _artifact(
            "nifty200.csv",
            "Index Name,Date,Open,High,Low,Close\n"
            "NIFTY 200,2021-01-04,100,110,90,105\n"
            "NIFTY 200,2021-01-04,100,110,90,105\n"
            "NIFTY 500,2021-01-05,100,110,90,105\n"
            "NIFTY 200,2021-01-06,100,90,95,96\n"
            "NIFTY 200,2099-01-01,100,110,90,105\n",
        ),
        requested_start=date(2021, 1, 1),
        requested_end=date(2099, 1, 1),
    )
    assert len(parsed.rows) == 1
    assert parsed.rejected_row_count == 4
    assert {issue.code for issue in parsed.issues} == {
        "DUPLICATE_INDEX_PRICE",
        "INVALID_INDEX_OHLC_ROW",
    }


def test_corporate_action_parser_recognizes_only_unambiguous_pit_rows():
    parsed = parse_corporate_actions(
        _artifact(
            "actions.csv",
            "Symbol,Series,Purpose,Ex-Date,Record Date,Source Published At\n"
            "ALPHA,EQ,Bonus Issue 1:1,2026-09-20,2026-09-21,2026-09-01T10:00:00+05:30\n"
            "ALPHA,EQ,Split from Rs 10 to Rs 2,2026-10-01,2026-10-02,2026-09-02T10:00:00Z\n"
            "ALPHA,EQ,Dividend Rs 5,2026-10-03,2026-10-04,2026-09-03T10:00:00Z\n"
            "ALPHA,EQ,Bonus shares,2026-10-05,2026-10-06,2026-09-04T10:00:00Z\n"
            "ALPHA,EQ,Bonus Issue 2:1,2026-10-07,2026-10-08,\n"
            "ALPHA,EQ,Bonus Issue 1:1,2026-09-20,2026-09-21,2026-09-01T10:00:00+05:30\n",
        )
    )
    assert [(row.action_type, row.ratio_numerator, row.ratio_denominator) for row in parsed.rows[:2]] == [
        ("BONUS", 1, 1),
        ("STOCK_SPLIT", 5, 1),
    ]
    assert parsed.rows[0].source_published_at.isoformat() == "2026-09-01T04:30:00+00:00"
    assert parsed.rows[2].source_published_at is None
    assert {issue.code for issue in parsed.issues} == {
        "UNSUPPORTED_CORPORATE_ACTION",
        "AMBIGUOUS_ACTION_RATIO",
        "AVAILABILITY_TIMESTAMP_UNKNOWN",
        "DUPLICATE_CORPORATE_ACTION",
    }
