from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.data.providers import DemoMarketDataProvider
from app.data.seed import seed_demo
from app.models import IndexDailyPrice, MarketIndex, SourceArtifact
from app.regimes.definitions import MARKET_REGIME_4_STATE_V1
from app.regimes.schemas import RegimeHistoryRequest
from app.regimes.service import (
    MarketRegimeService,
    classify_regime,
    nearest_rank_percentile,
)
from app.repositories.indices import IndexPriceRecord


REGIME_NAMESPACE = uuid.UUID("ec6e9d46-fb93-4a7e-b9ee-b9bb9ba8675e")


def _seed_demo_benchmark(db, *, reverse: bool = False):
    provider = DemoMarketDataProvider()
    benchmark = MarketIndex(
        id=uuid.uuid5(REGIME_NAMESPACE, "benchmark:NIFTYDEMO100"),
        name="NIFTY Demo 100",
        symbol="NIFTYDEMO100",
        provider=provider.code,
        exchange="NSE",
    )
    db.add(benchmark)
    db.flush()
    payloads = provider.get_index_daily_prices()
    if reverse:
        payloads.reverse()
    for payload in payloads:
        row = dict(payload)
        row.pop("index_symbol")
        db.add(
            IndexDailyPrice(
                id=uuid.uuid5(
                    REGIME_NAMESPACE,
                    f"price:{benchmark.id}:{row['trading_date']}",
                ),
                index_id=benchmark.id,
                **row,
            )
        )
    db.commit()
    return benchmark, provider.get_index_daily_prices()


def _history_request(payloads, **overrides):
    payload = {
        "benchmark": "NIFTYDEMO100",
        "start_date": payloads[0]["trading_date"],
        "end_date": payloads[-1]["trading_date"],
        "source_mode": "DEMO",
    }
    payload.update(overrides)
    return RegimeHistoryRequest(**payload)


@pytest.mark.parametrize(
    ("close", "sma_50", "sma_200", "momentum", "volatility", "threshold", "expected"),
    [
        ("110", "105", "100", "0.10", "0.20", "0.30", "TRENDING_BULL"),
        ("90", "95", "100", "-0.10", "0.20", "0.30", "TRENDING_BEAR"),
        ("100", "100", "100", "0", "0.20", "0.30", "SIDEWAYS"),
        ("110", "105", "100", "0.10", "0.30", "0.30", "HIGH_VOLATILITY"),
        ("90", "95", "100", "-0.10", "0.40", "0.30", "HIGH_VOLATILITY"),
    ],
)
def test_exact_v1_classification_and_precedence(
    close, sma_50, sma_200, momentum, volatility, threshold, expected
):
    result = classify_regime(
        close=Decimal(close),
        sma_50=Decimal(sma_50),
        sma_200=Decimal(sma_200),
        momentum_3m_63d=Decimal(momentum),
        volatility_20=Decimal(volatility),
        volatility_threshold=Decimal(threshold),
        prior_valid_volatility_count=126,
    )
    assert result[0] == expected


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sma_50", None),
        ("sma_200", None),
        ("momentum_3m_63d", None),
        ("volatility_20", None),
    ],
)
def test_missing_inputs_and_short_history_are_insufficient(field, value):
    inputs = {
        "close": Decimal("110"),
        "sma_50": Decimal("105"),
        "sma_200": Decimal("100"),
        "momentum_3m_63d": Decimal("0.10"),
        "volatility_20": Decimal("0.20"),
        "volatility_threshold": Decimal("0.30"),
        "prior_valid_volatility_count": 126,
    }
    inputs[field] = value
    assert classify_regime(**inputs)[0] == "INSUFFICIENT_HISTORY"
    inputs[field] = Decimal("0.20") if field == "volatility_20" else Decimal("100")
    inputs["prior_valid_volatility_count"] = 125
    assert classify_regime(**inputs)[0] == "INSUFFICIENT_HISTORY"


@pytest.mark.parametrize(
    ("close", "sma_50", "sma_200", "momentum"),
    [
        ("100", "105", "100", "0.1"),
        ("110", "100", "100", "0.1"),
        ("110", "105", "100", "0"),
    ],
)
def test_strict_trend_boundaries_fall_to_sideways(
    close, sma_50, sma_200, momentum
):
    result = classify_regime(
        close=Decimal(close),
        sma_50=Decimal(sma_50),
        sma_200=Decimal(sma_200),
        momentum_3m_63d=Decimal(momentum),
        volatility_20=Decimal("0.1"),
        volatility_threshold=Decimal("0.2"),
        prior_valid_volatility_count=126,
    )
    assert result[0] == "SIDEWAYS"


def test_nearest_rank_is_exact_and_current_observation_is_excluded():
    ordered = [Decimal("1")] * 101 + [Decimal("100")] * 25
    assert nearest_rank_percentile(ordered, Decimal("0.80")) == Decimal("1")
    assert nearest_rank_percentile(ordered + [Decimal("100")], Decimal("0.80")) == Decimal("100")

    records = []
    frame = []
    start = date(2024, 1, 1)
    for index, volatility in enumerate([*ordered, Decimal("100")]):
        day = start + timedelta(days=index)
        records.append(
            IndexPriceRecord(
                id=uuid.uuid4(),
                index_id=uuid.uuid4(),
                trading_date=day,
                open=Decimal("110"),
                high=Decimal("111"),
                low=Decimal("109"),
                close=Decimal("110"),
                source_mode="DEMO",
                source="TEST",
                available_at=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
                source_artifact_id=None,
                ingestion_run_id=None,
            )
        )
        frame.append(
            {
                "SMA_50": Decimal("105"),
                "SMA_200": Decimal("100"),
                "MOM_3M_63D": Decimal("0.1"),
                "VOLATILITY_20": volatility,
            }
        )
    last = MarketRegimeService._classify_timeline(
        records, frame, MARKET_REGIME_4_STATE_V1
    )[-1]
    assert last.prior_valid_volatility_count == 126
    assert last.historical_volatility_threshold == Decimal("1")
    assert last.classification == "HIGH_VOLATILITY"


def test_demo_history_derives_all_states_transitions_distribution_and_duration(db):
    _, payloads = _seed_demo_benchmark(db, reverse=True)
    service = MarketRegimeService(db)
    result = service.history(_history_request(payloads))
    repeated = service.history(_history_request(payloads))

    assert result.availability == "AVAILABLE"
    assert result.regime_timeline_fingerprint == repeated.regime_timeline_fingerprint
    assert [item.observation_date for item in result.classifications] == sorted(
        item.observation_date for item in result.classifications
    )
    assert {
        "TRENDING_BULL",
        "TRENDING_BEAR",
        "SIDEWAYS",
        "HIGH_VOLATILITY",
    } <= {item.classification for item in result.classifications}
    assert sum(item.session_count for item in result.distributions) == len(
        result.classifications
    )
    assert result.transitions
    latest = result.latest_classification
    assert latest is not None
    trailing = 0
    for item in reversed(result.classifications):
        if item.classification != latest.classification:
            break
        trailing += 1
    assert result.latest_regime_duration_sessions == trailing
    assert result.coverage.first_classifiable_date is not None


def test_regime_dataset_fingerprint_accepts_real_provenance_uuids(db):
    _, payloads = _seed_demo_benchmark(db)
    artifact = SourceArtifact(
        provider="OFFICIAL_NSE_INDICES_PUBLIC",
        artifact_type="NIFTY_200_INDEX_HISTORY",
        source_date=payloads[-1]["trading_date"],
        original_file_name="fixture.json",
        source_locator="fixture:official",
        imported_at=datetime.now(UTC),
        sha256="a" * 64,
        byte_size=2,
        parser_code="NSE_INDICES_HISTORICAL_OHLC",
        parser_version="1.0.0",
        normalized_fingerprint="b" * 64,
        parse_status="SUCCEEDED",
    )
    db.add(artifact)
    db.flush()
    first_price = db.scalar(
        select(IndexDailyPrice).order_by(IndexDailyPrice.trading_date).limit(1)
    )
    first_price.source_artifact_id = artifact.id
    db.commit()

    request = _history_request(payloads)
    first = MarketRegimeService(db).history(request)
    second = MarketRegimeService(db).history(request)
    assert first.benchmark_dataset_fingerprint == second.benchmark_dataset_fingerprint


def test_future_prices_availability_and_spikes_cannot_change_earlier_timeline(db):
    benchmark, payloads = _seed_demo_benchmark(db)
    cutoff_payload = payloads[900]
    cutoff = cutoff_payload["trading_date"]
    as_of = cutoff_payload["available_at"]
    request = _history_request(payloads, end_date=cutoff, as_of=as_of)
    before = MarketRegimeService(db).history(request)

    future_day = payloads[-1]["trading_date"] + timedelta(days=3)
    db.add(
        IndexDailyPrice(
            index_id=benchmark.id,
            trading_date=future_day,
            open=Decimal("1000"),
            high=Decimal("2000"),
            low=Decimal("500"),
            close=Decimal("1800"),
            source_mode="DEMO",
            source="FUTURE_SPIKE",
            available_at=datetime.combine(future_day, datetime.min.time(), tzinfo=UTC),
        )
    )
    hidden_day = cutoff - timedelta(days=1)
    while hidden_day.weekday() < 5:
        hidden_day -= timedelta(days=1)
    db.add(
        IndexDailyPrice(
            index_id=benchmark.id,
            trading_date=hidden_day,
            open=Decimal("700"),
            high=Decimal("900"),
            low=Decimal("600"),
            close=Decimal("850"),
            source_mode="DEMO",
            source="LATE_UNAVAILABLE",
            available_at=as_of + timedelta(days=1),
        )
    )
    db.commit()
    after = MarketRegimeService(db).history(request)
    assert before.regime_timeline_fingerprint == after.regime_timeline_fingerprint
    assert [item.evaluation_fingerprint for item in before.classifications] == [
        item.evaluation_fingerprint for item in after.classifications
    ]


def test_missing_source_is_truthful_and_api_validation_is_bounded(db, client):
    _, payloads = _seed_demo_benchmark(db)
    unavailable = MarketRegimeService(db).history(
        _history_request(payloads, source_mode="OFFICIAL")
    )
    assert unavailable.availability == "UNAVAILABLE"
    assert unavailable.classifications == []
    assert "BENCHMARK_SOURCE_COVERAGE_UNAVAILABLE" in unavailable.warnings

    metadata = client.get("/api/v1/regimes/metadata")
    assert metadata.status_code == 200
    assert metadata.json()["definitions"][0]["code"] == "MARKET_REGIME_4_STATE"
    history = client.post(
        "/api/v1/regimes/history",
        json=_history_request(payloads).model_dump(mode="json"),
    )
    assert history.status_code == 200
    assert history.json()["availability"] == "AVAILABLE"
    invalid = client.post(
        "/api/v1/regimes/history",
        json={
            "benchmark": "NIFTYDEMO100",
            "start_date": "2025-02-01",
            "end_date": "2025-01-01",
            "source_mode": "DEMO",
        },
    )
    assert invalid.status_code == 422


def test_feature_registry_counts_and_forbidden_indicator_remain_unchanged():
    from app.technical.definitions import CORE_TECHNICAL_SET, FEATURE_DEFINITIONS, STRATEGY_TECHNICAL_SET

    assert len(CORE_TECHNICAL_SET.feature_codes) == 36
    assert len(STRATEGY_TECHNICAL_SET.feature_codes) == 38
    assert "EMA_200" not in FEATURE_DEFINITIONS


def test_history_request_rejects_naive_as_of():
    with pytest.raises(ValidationError, match="timezone"):
        RegimeHistoryRequest(
            benchmark="NIFTYDEMO100",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 2, 1),
            source_mode="DEMO",
            as_of=datetime(2025, 2, 1),
        )


def test_demo_benchmark_seed_is_idempotent_and_explicit(db):
    first = seed_demo(db)
    second = seed_demo(db)
    count = db.scalar(select(func.count()).select_from(IndexDailyPrice))
    assert first["index_prices"] == 1300
    assert second["index_prices"] == 0
    assert count == 1300
    assert set(db.scalars(select(IndexDailyPrice.source_mode))) == {"DEMO"}
