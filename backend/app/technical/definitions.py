from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeatureDefinition:
    code: str
    name: str
    category: str
    description: str
    formula: str
    required_fields: tuple[str, ...]
    lookback_sessions: int
    minimum_observations: int
    output_type: str
    unit: str
    parameters: dict[str, int | str]
    version: str = "1"
    adjustment_support: str = "RAW_OR_ADJUSTED"
    availability_semantics: str = "Available no earlier than the source exchange session close."


@dataclass(frozen=True)
class FeatureSetDefinition:
    code: str
    version: str
    name: str
    description: str
    feature_codes: tuple[str, ...]
    default_adjustment_policy: str = "ADJUSTED"
    is_active: bool = True


def _feature(
    code: str,
    name: str,
    category: str,
    description: str,
    formula: str,
    required_fields: tuple[str, ...],
    lookback: int,
    minimum: int,
    output_type: str,
    unit: str,
    **parameters: int | str,
) -> FeatureDefinition:
    return FeatureDefinition(code, name, category, description, formula, required_fields, lookback, minimum, output_type, unit, parameters)


_CORE_FEATURES = [
    *[
        _feature(f"RET_{n}D", f"{n}-session return", "returns", f"Simple close-to-close return over {n} observed sessions.", f"close[t] / close[t-{n}] - 1", ("close",), n, n + 1, "DECIMAL", "DECIMAL_FRACTION", sessions=n)
        for n in (1, 5, 10, 20)
    ],
    *[
        _feature(code, name, "momentum", f"Simple close momentum over {n} observed sessions.", f"close[t] / close[t-{n}] - 1", ("close",), n, n + 1, "DECIMAL", "DECIMAL_FRACTION", sessions=n)
        for code, name, n in (("MOM_1M_21D", "1-month momentum", 21), ("MOM_3M_63D", "3-month momentum", 63), ("MOM_6M_126D", "6-month momentum", 126))
    ],
    *[
        _feature(f"SMA_{n}", f"{n}-session simple moving average", "trend", f"Arithmetic mean of the latest {n} closes, including the current session.", f"mean(close[t-{n - 1}:t])", ("close",), n - 1, n, "DECIMAL", "PRICE", sessions=n)
        for n in (20, 50, 100, 200)
    ],
    *[
        _feature(f"EMA_{n}", f"{n}-session exponential moving average", "trend", f"EMA with alpha 2/({n}+1), seeded with the first {n}-close arithmetic mean.", "alpha*close[t] + (1-alpha)*EMA[t-1]", ("close",), n - 1, n, "DECIMAL", "PRICE", sessions=n, seed="SMA")
        for n in (20, 50)
    ],
    *[
        _feature(f"DISTANCE_SMA_{n}", f"Distance from SMA {n}", "trend", f"Fractional distance of close from the {n}-session SMA.", f"close[t] / SMA_{n}[t] - 1", ("close",), n - 1, n, "DECIMAL", "DECIMAL_FRACTION", sessions=n)
        for n in (20, 50, 200)
    ],
    _feature("RSI_14", "Wilder RSI 14", "momentum", "Wilder relative-strength index with a 14-change arithmetic seed and recursive smoothing.", "100 - 100/(1 + avg_gain/avg_loss)", ("close",), 14, 15, "DECIMAL", "INDEX_0_100", sessions=14, smoothing="WILDER"),
    _feature("TRUE_RANGE", "True range", "volatility", "Maximum of high-low and the absolute high/low gaps from previous close; first row uses high-low.", "max(high-low, abs(high-prev_close), abs(low-prev_close))", ("high", "low", "close"), 1, 1, "DECIMAL", "PRICE"),
    _feature("ATR_14", "Wilder ATR 14", "volatility", "Wilder average true range seeded by the first 14 true-range values.", "(13*ATR[t-1] + TR[t]) / 14", ("high", "low", "close"), 13, 14, "DECIMAL", "PRICE", sessions=14, smoothing="WILDER"),
    _feature("ATR_PCT_14", "ATR percent 14", "volatility", "ATR 14 divided by current close.", "ATR_14[t] / close[t]", ("high", "low", "close"), 13, 14, "DECIMAL", "DECIMAL_FRACTION", sessions=14),
    *[
        _feature(f"VOLATILITY_{n}", f"{n}-session annualized volatility", "volatility", f"Sample standard deviation of {n} daily simple returns, annualized by sqrt(252).", f"stdev_sample(RET_1D, {n}) * sqrt(252)", ("close",), n, n + 1, "DECIMAL", "DECIMAL_FRACTION", sessions=n, ddof=1, annualization=252)
        for n in (20, 60)
    ],
    *[
        _feature(f"AVG_VOLUME_{n}", f"{n}-session average volume", "liquidity", f"Arithmetic mean of volume over {n} observations, including the current session.", f"mean(volume[t-{n - 1}:t])", ("volume",), n - 1, n, "DECIMAL", "SHARES", sessions=n)
        for n in (20, 50)
    ],
    *[
        _feature(f"VOLUME_RATIO_{n}", f"Volume ratio {n}", "liquidity", f"Current volume divided by its {n}-session average.", f"volume[t] / AVG_VOLUME_{n}[t]", ("volume",), n - 1, n, "DECIMAL", "RATIO", sessions=n)
        for n in (20, 50)
    ],
    _feature("AVG_TRADED_VALUE_20", "20-session average traded value", "liquidity", "Arithmetic mean of 20 valid traded-value observations; missing values are not fabricated.", "mean(traded_value[t-19:t])", ("traded_value",), 19, 20, "DECIMAL", "CURRENCY", sessions=20),
    _feature("GAP_PCT_1D", "Opening gap", "session", "Opening price relative to the previous observed close.", "open[t] / close[t-1] - 1", ("open", "close"), 1, 2, "DECIMAL", "DECIMAL_FRACTION"),
    _feature("INTRADAY_RETURN", "Intraday return", "session", "Close relative to the same-session open.", "close[t] / open[t] - 1", ("open", "close"), 0, 1, "DECIMAL", "DECIMAL_FRACTION"),
    _feature("RANGE_PCT", "Session range percent", "session", "High-low range divided by close.", "(high[t] - low[t]) / close[t]", ("high", "low", "close"), 0, 1, "DECIMAL", "DECIMAL_FRACTION"),
    _feature("CLOSE_LOCATION", "Close location", "session", "Close position inside the session range; undefined when high equals low.", "(close[t] - low[t]) / (high[t] - low[t])", ("high", "low", "close"), 0, 1, "DECIMAL", "RATIO"),
    _feature("ABOVE_SMA_20", "Close above SMA 20", "trend_state", "Descriptive comparison of close and SMA 20; not a trading signal.", "close[t] > SMA_20[t]", ("close",), 19, 20, "BOOLEAN", "BOOLEAN"),
    _feature("ABOVE_SMA_50", "Close above SMA 50", "trend_state", "Descriptive comparison of close and SMA 50; not a trading signal.", "close[t] > SMA_50[t]", ("close",), 49, 50, "BOOLEAN", "BOOLEAN"),
    _feature("ABOVE_SMA_200", "Close above SMA 200", "trend_state", "Descriptive comparison of close and SMA 200; not a trading signal.", "close[t] > SMA_200[t]", ("close",), 199, 200, "BOOLEAN", "BOOLEAN"),
    _feature("SMA_20_ABOVE_50", "SMA 20 above SMA 50", "trend_state", "Descriptive moving-average ordering; not a trading signal.", "SMA_20[t] > SMA_50[t]", ("close",), 49, 50, "BOOLEAN", "BOOLEAN"),
    _feature("SMA_50_ABOVE_200", "SMA 50 above SMA 200", "trend_state", "Descriptive moving-average ordering; not a trading signal.", "SMA_50[t] > SMA_200[t]", ("close",), 199, 200, "BOOLEAN", "BOOLEAN"),
]

_STRATEGY_SUPPORT_FEATURES = [
    _feature(
        "PRIOR_HIGH_20",
        "Prior 20-session high",
        "price_structure",
        "Maximum high over the preceding 20 completed sessions, excluding the current session.",
        "max(high[t-20:t])",
        ("high",),
        20,
        21,
        "DECIMAL",
        "PRICE",
        sessions=20,
        current_session="EXCLUDED",
    ),
    _feature(
        "BREAKOUT_PCT_20",
        "20-session breakout percent",
        "price_structure",
        "Current close divided by the preceding 20-session high, minus one.",
        "close[t] / PRIOR_HIGH_20[t] - 1",
        ("close", "high"),
        20,
        21,
        "DECIMAL",
        "DECIMAL_FRACTION",
        sessions=20,
        current_session="EXCLUDED_FROM_PRIOR_HIGH",
    ),
]

CORE_FEATURE_DEFINITIONS: dict[str, FeatureDefinition] = {
    feature.code: feature for feature in _CORE_FEATURES
}
FEATURE_DEFINITIONS: dict[str, FeatureDefinition] = {
    **CORE_FEATURE_DEFINITIONS,
    **{feature.code: feature for feature in _STRATEGY_SUPPORT_FEATURES},
}

CORE_TECHNICAL_SET = FeatureSetDefinition(
    code="ALPHADESK_CORE_TECHNICAL",
    version="1",
    name="AlphaDesk Core Technical",
    description="Point-in-time-safe daily price, momentum, trend, volatility, liquidity, session, and descriptive trend-state features.",
    feature_codes=tuple(CORE_FEATURE_DEFINITIONS),
)

STRATEGY_TECHNICAL_SET = FeatureSetDefinition(
    code="ALPHADESK_STRATEGY_TECHNICAL",
    version="1",
    name="AlphaDesk Strategy Technical",
    description="Core technical v1 plus generic point-in-time price-structure features for versioned strategy research.",
    feature_codes=tuple(FEATURE_DEFINITIONS),
)

FEATURE_SETS: dict[tuple[str, str], FeatureSetDefinition] = {
    (CORE_TECHNICAL_SET.code, CORE_TECHNICAL_SET.version): CORE_TECHNICAL_SET,
    (STRATEGY_TECHNICAL_SET.code, STRATEGY_TECHNICAL_SET.version): STRATEGY_TECHNICAL_SET,
}
