from datetime import date, timedelta
from decimal import Decimal
from math import isfinite
from statistics import stdev

import pytest

from app.technical.calculators import PricePoint, calculate_feature_frame
from app.strategies.definitions import INITIAL_STRATEGIES
from app.technical.definitions import (
    CORE_TECHNICAL_SET,
    FEATURE_DEFINITIONS,
    STRATEGY_TECHNICAL_SET,
)


def point(index: int, *, close: Decimal | None = None, volume: int | None = None, traded: bool = True) -> PricePoint:
    value = close if close is not None else Decimal(index)
    resolved_volume = volume if volume is not None else 1000 + index
    return PricePoint(
        trading_date=date(2024, 1, 1) + timedelta(days=index),
        open=value - Decimal("0.5"),
        high=value + Decimal(2),
        low=value - Decimal(2),
        close=value,
        volume=resolved_volume,
        traded_value=value * resolved_volume if traded else None,
        source="GOLDEN",
    )


def test_catalog_contains_the_complete_immutable_v1_set():
    assert len(CORE_TECHNICAL_SET.feature_codes) == 36
    assert len(FEATURE_DEFINITIONS) == 38
    assert tuple(FEATURE_DEFINITIONS)[:36] == CORE_TECHNICAL_SET.feature_codes
    assert {definition.version for definition in FEATURE_DEFINITIONS.values()} == {"1"}
    assert all(definition.formula and definition.required_fields for definition in FEATURE_DEFINITIONS.values())


def test_golden_vector_covers_every_feature_group_and_long_windows():
    points = [point(index) for index in range(1, 221)]
    latest = calculate_feature_frame(points)[-1]

    assert float(latest["RET_1D"]) == pytest.approx(220 / 219 - 1)
    assert latest["RET_20D"] == Decimal("0.1")
    assert float(latest["MOM_6M_126D"]) == pytest.approx(220 / 94 - 1)
    assert latest["SMA_20"] == Decimal("210.5")
    assert latest["SMA_200"] == Decimal("120.5")
    assert latest["EMA_20"] == Decimal("210.5")
    assert latest["EMA_50"] == Decimal("195.5")
    assert float(latest["DISTANCE_SMA_200"]) == pytest.approx(220 / 120.5 - 1)
    assert latest["RSI_14"] == 100
    assert latest["TRUE_RANGE"] == 4
    assert latest["ATR_14"] == 4
    assert float(latest["ATR_PCT_14"]) == pytest.approx(4 / 220)

    expected_volatility = stdev([float(Decimal(day) / Decimal(day - 1) - 1) for day in range(201, 221)]) * (252**0.5)
    assert float(latest["VOLATILITY_20"]) == pytest.approx(expected_volatility)
    assert latest["AVG_VOLUME_20"] == Decimal("1210.5")
    assert float(latest["VOLUME_RATIO_20"]) == pytest.approx(1220 / 1210.5)
    expected_traded = sum((Decimal(day) * Decimal(1000 + day) for day in range(201, 221)), Decimal(0)) / 20
    assert latest["AVG_TRADED_VALUE_20"] == expected_traded
    assert float(latest["GAP_PCT_1D"]) == pytest.approx(219.5 / 219 - 1)
    assert float(latest["INTRADAY_RETURN"]) == pytest.approx(220 / 219.5 - 1)
    assert float(latest["RANGE_PCT"]) == pytest.approx(4 / 220)
    assert latest["CLOSE_LOCATION"] == Decimal("0.5")
    assert latest["ABOVE_SMA_20"] is True
    assert latest["ABOVE_SMA_50"] is True
    assert latest["ABOVE_SMA_200"] is True
    assert latest["SMA_20_ABOVE_50"] is True
    assert latest["SMA_50_ABOVE_200"] is True
    assert set(latest) == set(FEATURE_DEFINITIONS)


def test_warmup_boundaries_are_null_until_exact_minimum_history():
    frame = calculate_feature_frame([point(index) for index in range(1, 202)])
    assert frame[18]["SMA_20"] is None
    assert frame[19]["SMA_20"] is not None
    assert frame[13]["RSI_14"] is None
    assert frame[14]["RSI_14"] == 100
    assert frame[12]["ATR_14"] is None
    assert frame[13]["ATR_14"] is not None
    assert frame[19]["VOLATILITY_20"] is None
    assert frame[20]["VOLATILITY_20"] is not None
    assert frame[198]["SMA_200"] is None
    assert frame[199]["SMA_200"] is not None


def test_rsi_flat_and_falling_edge_conventions():
    flat = calculate_feature_frame([point(index, close=Decimal(50)) for index in range(1, 17)])[-1]
    falling = calculate_feature_frame([point(index, close=Decimal(100 - index)) for index in range(1, 17)])[-1]
    assert flat["RSI_14"] == 50
    assert falling["RSI_14"] == 0


def test_invalid_denominators_and_missing_traded_values_return_null_not_non_finite():
    zero = PricePoint(date(2025, 1, 1), Decimal(0), Decimal(0), Decimal(0), Decimal(0), 0, None, "EDGE")
    values = calculate_feature_frame([zero] * 21)[-1]
    assert values["RET_1D"] is None
    assert values["ATR_PCT_14"] is None
    assert values["VOLUME_RATIO_20"] is None
    assert values["INTRADAY_RETURN"] is None
    assert values["RANGE_PCT"] is None
    assert values["CLOSE_LOCATION"] is None
    assert values["AVG_TRADED_VALUE_20"] is None
    assert all(isfinite(float(value)) for value in values.values() if isinstance(value, Decimal))


@pytest.mark.parametrize("profile", ["constant", "small", "large", "gapped"])
def test_selected_composition_timeline_is_exact_subset_of_authoritative_frame(profile):
    points: list[PricePoint] = []
    for index in range(230):
        if profile == "constant":
            close = Decimal("50")
        elif profile == "small":
            close = Decimal("0.0001") + Decimal(index) * Decimal("0.000001")
        elif profile == "large":
            close = Decimal("999999999999") + Decimal(index) * Decimal("12345.6789")
        else:
            close = Decimal("100") + Decimal(index % 11) * Decimal("17.25")
        open_price = close if index % 9 else close * Decimal("0.75")
        spread = close * (Decimal("0.90") if index % 17 == 0 else Decimal("0.01"))
        low = max(Decimal(0), min(open_price, close) - spread)
        high = max(open_price, close) + spread
        volume = 0 if index % 23 == 0 else index + 1
        points.append(
            PricePoint(
                trading_date=date(2020, 1, 1) + timedelta(days=index),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                traded_value=close * volume if index % 29 else None,
                source="PHASE10_EDGE",
            )
        )

    selected_codes = tuple(
        sorted({rule.feature_code for definition in INITIAL_STRATEGIES for rule in definition.rules})
    )
    assert set(selected_codes).issubset(STRATEGY_TECHNICAL_SET.feature_codes)
    full = calculate_feature_frame(points)
    selected = calculate_feature_frame(points, selected_codes)
    assert selected == [
        {code: row[code] for code in selected_codes}
        for row in full
    ]
