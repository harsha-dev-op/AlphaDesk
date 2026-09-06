from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True, slots=True)
class BacktestProfileDefinition:
    profile_code: str
    profile_version: str
    display_name: str
    compatible_strategies: tuple[str, ...]
    default_holding_sessions: tuple[tuple[str, int], ...]
    entry_timing: Literal["NEXT_SESSION_OPEN"]
    exit_timing: Literal["OPEN_AFTER_HOLDING_SESSIONS"]
    default_slippage_bps: Decimal
    default_trade_notional_inr: Decimal
    quantity_policy: Literal["INTEGER_SHARES_FLOOR"]
    stop_policy: Literal["OPTIONAL_DAILY_RAW_OHLC_STOP_FIRST"]
    target_policy: Literal["OPTIONAL_DAILY_RAW_OHLC_STOP_FIRST"]
    overlap_policy: Literal["ONE_OPEN_TRADE_PER_SECURITY"]
    end_policy: Literal["FORCE_CLOSE_LAST_AVAILABLE_CLOSE"]
    missing_entry_policy: Literal["SKIP_IMMEDIATE_NEXT_SESSION"]
    missing_exit_policy: Literal["BOUNDED_FORWARD_SEARCH"]
    default_max_exit_delay_sessions: int
    cost_model_code: str
    cost_model_version: str

    def holding_sessions_for(self, strategy_code: str) -> int:
        return dict(self.default_holding_sessions)[strategy_code]


@dataclass(frozen=True, slots=True)
class CostModelDefinition:
    code: str
    version: str
    display_name: str
    stt_buy_rate: Decimal
    stt_sell_rate: Decimal
    exchange_transaction_rate: Decimal
    sebi_turnover_rate: Decimal
    gst_rate: Decimal
    stamp_duty_buy_rate: Decimal
    gst_taxable_components: tuple[str, ...]
    monetary_rounding: Literal["PAISE_HALF_UP"]
    stt_rounding: Literal["RUPEE_HALF_UP_PER_LEG"]
    default_brokerage_per_order_inr: Decimal
    default_brokerage_rate: Decimal
    default_dp_charge_per_scrip_sell_day_inr: Decimal


NEXT_OPEN_FIXED_HOLD_V1 = BacktestProfileDefinition(
    profile_code="NEXT_OPEN_FIXED_HOLD",
    profile_version="1",
    display_name="Next-open fixed-hold independent trade simulation",
    compatible_strategies=("MOMENTUM_TREND", "BREAKOUT_20D", "MEAN_REVERSION_PULLBACK"),
    default_holding_sessions=(
        ("MOMENTUM_TREND", 20),
        ("BREAKOUT_20D", 20),
        ("MEAN_REVERSION_PULLBACK", 10),
    ),
    entry_timing="NEXT_SESSION_OPEN",
    exit_timing="OPEN_AFTER_HOLDING_SESSIONS",
    default_slippage_bps=Decimal("5"),
    default_trade_notional_inr=Decimal("100000"),
    quantity_policy="INTEGER_SHARES_FLOOR",
    stop_policy="OPTIONAL_DAILY_RAW_OHLC_STOP_FIRST",
    target_policy="OPTIONAL_DAILY_RAW_OHLC_STOP_FIRST",
    overlap_policy="ONE_OPEN_TRADE_PER_SECURITY",
    end_policy="FORCE_CLOSE_LAST_AVAILABLE_CLOSE",
    missing_entry_policy="SKIP_IMMEDIATE_NEXT_SESSION",
    missing_exit_policy="BOUNDED_FORWARD_SEARCH",
    default_max_exit_delay_sessions=5,
    cost_model_code="INDIA_NSE_CASH_DELIVERY_2026_09",
    cost_model_version="1",
)


INDIA_NSE_CASH_DELIVERY_2026_09_V1 = CostModelDefinition(
    code="INDIA_NSE_CASH_DELIVERY_2026_09",
    version="1",
    display_name="India NSE cash delivery — September 2026 research assumptions",
    stt_buy_rate=Decimal("0.001"),
    stt_sell_rate=Decimal("0.001"),
    exchange_transaction_rate=Decimal("0.0000307"),
    sebi_turnover_rate=Decimal("0.000001"),
    gst_rate=Decimal("0.18"),
    stamp_duty_buy_rate=Decimal("0.00015"),
    gst_taxable_components=("BROKERAGE", "EXCHANGE_TRANSACTION_CHARGE", "SEBI_CHARGE"),
    monetary_rounding="PAISE_HALF_UP",
    stt_rounding="RUPEE_HALF_UP_PER_LEG",
    default_brokerage_per_order_inr=Decimal("0"),
    default_brokerage_rate=Decimal("0"),
    default_dp_charge_per_scrip_sell_day_inr=Decimal("0"),
)
