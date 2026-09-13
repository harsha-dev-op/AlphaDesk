from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True, slots=True)
class PortfolioPolicyDefinition:
    policy_code: str
    policy_version: str
    display_name: str
    compatible_profiles: tuple[str, ...]
    default_initial_capital_inr: Decimal
    default_max_concurrent_positions: int
    default_max_position_weight: Decimal
    default_max_gross_exposure: Decimal
    default_minimum_cash_reserve_pct: Decimal
    allocation_policy: Literal["EQUAL_SLOT_AT_SESSION_START_EQUITY"]
    candidate_selection_policy: Literal["SIGNAL_DATE_THEN_SYMBOL"]
    integer_share_policy: Literal["LARGEST_AFFORDABLE_INTEGER_INCLUDING_COSTS"]
    same_security_policy: Literal["ONE_OPEN_POSITION_PER_SECURITY"]
    same_session_event_order: tuple[str, ...]
    end_policy: Literal["FORCE_CLOSE_LAST_AVAILABLE_CLOSE"]
    mark_to_market_policy: Literal["DAILY_RAW_CLOSE_MARK_TO_LAST"]
    risk_free_rate_convention: Literal["ANNUAL_EFFECTIVE_TO_252_SESSION_DAILY"]
    leverage_policy: Literal["LONG_ONLY_NO_LEVERAGE"]
    rebalancing_policy: Literal["EVENT_DRIVEN_NO_REBALANCING"]


LONG_ONLY_EQUAL_SLOT_PORTFOLIO_V1 = PortfolioPolicyDefinition(
    policy_code="LONG_ONLY_EQUAL_SLOT_PORTFOLIO",
    policy_version="1",
    display_name="Long-only equal-slot shared-capital portfolio",
    compatible_profiles=("NEXT_OPEN_FIXED_HOLD",),
    default_initial_capital_inr=Decimal("1000000"),
    default_max_concurrent_positions=10,
    default_max_position_weight=Decimal("0.10"),
    default_max_gross_exposure=Decimal("1.00"),
    default_minimum_cash_reserve_pct=Decimal("0.00"),
    allocation_policy="EQUAL_SLOT_AT_SESSION_START_EQUITY",
    candidate_selection_policy="SIGNAL_DATE_THEN_SYMBOL",
    integer_share_policy="LARGEST_AFFORDABLE_INTEGER_INCLUDING_COSTS",
    same_security_policy="ONE_OPEN_POSITION_PER_SECURITY",
    same_session_event_order=(
        "PRE_OPEN_CORPORATE_ACTIONS",
        "OPEN_TIME_EXITS",
        "SESSION_START_STATE",
        "NEW_ENTRY_CANDIDATES",
        "NEW_ENTRIES",
        "INTRADAY_EXITS",
        "CLOSE_MARK",
    ),
    end_policy="FORCE_CLOSE_LAST_AVAILABLE_CLOSE",
    mark_to_market_policy="DAILY_RAW_CLOSE_MARK_TO_LAST",
    risk_free_rate_convention="ANNUAL_EFFECTIVE_TO_252_SESSION_DAILY",
    leverage_policy="LONG_ONLY_NO_LEVERAGE",
    rebalancing_policy="EVENT_DRIVEN_NO_REBALANCING",
)


__all__ = ["LONG_ONLY_EQUAL_SLOT_PORTFOLIO_V1", "PortfolioPolicyDefinition"]
