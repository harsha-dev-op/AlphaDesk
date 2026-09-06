from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from math import isfinite

from app.models import DailyPrice, DataIngestionRun, Security, TradingCalendar


def seed_api_data(db):
    security = Security(exchange="NSE", symbol="APISEC", trading_symbol="APISEC-EQ", company_name="API Security", security_type="EQUITY", currency="INR", is_active=True)
    db.add(security)
    db.flush()
    for index in range(25):
        day = date(2025, 1, 1) + timedelta(days=index)
        value = Decimal(100 + index)
        db.add(DailyPrice(security_id=security.id, trading_date=day, open=value, high=value + 2, low=value - 2, close=value, volume=1000 + index, traded_value=value * (1000 + index), source="API_TEST"))
        db.add(TradingCalendar(exchange="NSE", trading_date=day, is_trading_day=True, session_close=time(15, 30)))
    db.add(DataIngestionRun(dataset_code="api_fixture", dataset_version="2025.v1", provider="API_TEST", status="SUCCESS", completed_at=datetime.now(UTC), records_written=25))
    db.commit()


def test_catalog_and_feature_set_endpoints_are_typed_and_versioned(client):
    catalog = client.get("/api/v1/features/catalog")
    sets = client.get("/api/v1/feature-sets")
    assert catalog.status_code == 200
    assert catalog.json()["count"] == 38
    assert {item["code"] for item in catalog.json()["definitions"]} >= {"SMA_200", "RSI_14", "ATR_14", "AVG_TRADED_VALUE_20"}
    assert sets.status_code == 200
    assert sets.json()["feature_sets"][0]["code"] == "ALPHADESK_CORE_TECHNICAL"
    assert sets.json()["feature_sets"][0]["version"] == "1"
    assert sets.json()["feature_sets"][1]["code"] == "ALPHADESK_STRATEGY_TECHNICAL"


def test_security_feature_endpoint_returns_provenance_range_and_null_warmups(db, client):
    seed_api_data(db)
    response = client.get("/api/v1/securities/apisec/features", params={"start_date": "2025-01-20", "end_date": "2025-01-25", "adjustment_policy": "raw", "as_of": "2026-01-01T00:00:00Z"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "APISEC"
    assert payload["adjustment_policy"] == "RAW"
    assert payload["feature_versions"]["SMA_200"] == "1"
    assert payload["dataset"]["dataset_version"] == "2025.v1"
    assert len(payload["dataset"]["fingerprint"]) == 64
    assert len(payload["items"]) == 6
    assert payload["items"][0]["values"]["SMA_20"] is not None
    assert payload["items"][0]["values"]["SMA_50"] is None
    assert payload["items"][0]["unavailable"]["SMA_50"] == "Requires 50 observations"
    for item in payload["items"]:
        for value in item["values"].values():
            if isinstance(value, str):
                assert isfinite(float(value))


def test_feature_api_validation_and_point_in_time_cutoff(db, client):
    seed_api_data(db)
    assert client.get("/api/v1/securities/missing/features").status_code == 404
    assert client.get("/api/v1/securities/apisec/features", params={"start_date": "2025-02-01", "end_date": "2025-01-01"}).status_code == 422
    assert client.get("/api/v1/securities/apisec/features", params={"feature_set_version": "2"}).status_code == 404
    assert client.get("/api/v1/securities/apisec/features", params={"adjustment_policy": "total_return"}).status_code == 422
    assert client.get("/api/v1/securities/apisec/features", params={"adjustment_policy": "adjusted", "as_of": "2026-01-01T00:00:00Z"}).status_code == 200
    assert client.get("/api/v1/securities/apisec/features", params={"as_of": "2025-01-01T10:00:00"}).status_code == 422
    before_close = client.get("/api/v1/securities/apisec/features", params={"end_date": "2025-01-01", "as_of": "2025-01-01T15:29:59+05:30"})
    after_close = client.get("/api/v1/securities/apisec/features", params={"end_date": "2025-01-01", "as_of": "2025-01-01T15:30:00+05:30"})
    assert before_close.json()["items"] == []
    assert len(after_close.json()["items"]) == 1
