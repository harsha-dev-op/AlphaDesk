from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.backtests import (
    AdjustmentPolicy,
    AggregateCostBreakdown,
    CorporateActionEffect,
    CostBreakdown,
    CostModelMetadata,
    DatasetMetadata,
    ParameterBounds,
    ProfileMetadata,
)
from app.schemas.scanner import ScannerUniverseMetadata
from app.schemas.strategies import StrategyMetadata, StrategyScalar

CandidateRejectionReason = Literal[
    "ALREADY_OPEN_POSITION",
    "PORTFOLIO_CAPACITY_REACHED",
    "INSUFFICIENT_PORTFOLIO_CASH",
    "GROSS_EXPOSURE_LIMIT",
    "MAX_POSITION_WEIGHT_LIMIT",
    "ENTRY_PRICE_UNAVAILABLE",
    "ENTRY_OUTSIDE_TEST_RANGE",
]
LedgerEventType = Literal[
    "INITIAL_CAPITAL",
    "ENTRY_PRINCIPAL",
    "ENTRY_COST",
    "EXIT_PROCEEDS",
    "EXIT_COST",
]
PortfolioExitReason = Literal[
    "TIME_EXIT",
    "STOP_LOSS",
    "PROFIT_TARGET",
    "FORCED_END_OF_TEST",
    "EXIT_PRICE_UNAVAILABLE",
]


class PortfolioRunRequest(BaseModel):
    strategy_code: str = Field(min_length=1, max_length=64)
    strategy_version: str = Field(default="1", min_length=1, max_length=32)
    parameter_overrides: dict[str, StrategyScalar] = Field(default_factory=dict)
    universe: str = Field(min_length=1, max_length=128)
    start_date: date
    end_date: date
    adjustment_policy: AdjustmentPolicy = "ADJUSTED"
    profile_code: str = "NEXT_OPEN_FIXED_HOLD"
    profile_version: str = "1"
    holding_sessions: int | None = Field(default=None, ge=1, le=252)
    slippage_bps: Decimal = Field(default=Decimal("5"), ge=Decimal("0"), le=Decimal("1000"))
    stop_loss_pct: Decimal | None = Field(default=None, gt=Decimal("0"), lt=Decimal("1"))
    profit_target_pct: Decimal | None = Field(default=None, gt=Decimal("0"), le=Decimal("10"))
    max_exit_delay_sessions: int = Field(default=5, ge=0, le=20)
    cost_model_code: str = "INDIA_NSE_CASH_DELIVERY_2026_09"
    cost_model_version: str = "1"
    brokerage_per_order_inr: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), le=Decimal("100000"))
    brokerage_rate: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), le=Decimal("0.1"))
    dp_charge_per_scrip_sell_day_inr: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), le=Decimal("100000"))
    portfolio_policy_code: str = "LONG_ONLY_EQUAL_SLOT_PORTFOLIO"
    portfolio_policy_version: str = "1"
    initial_capital_inr: Decimal = Field(default=Decimal("1000000"), gt=Decimal("0"), le=Decimal("100000000000"))
    max_concurrent_positions: int = Field(default=10, ge=1, le=500)
    max_position_weight: Decimal = Field(default=Decimal("0.10"), gt=Decimal("0"), le=Decimal("1"))
    max_gross_exposure: Decimal = Field(default=Decimal("1.00"), gt=Decimal("0"), le=Decimal("1"))
    minimum_cash_reserve_pct: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0"), lt=Decimal("1"))
    risk_free_rate_annual: Decimal = Field(default=Decimal("0.00"), gt=Decimal("-1"), le=Decimal("10"))
    out_of_sample_start_date: date | None = None
    position_detail_limit: int = Field(default=500, ge=0, le=5000)
    ledger_detail_limit: int = Field(default=1000, ge=0, le=10000)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "strategy_code": "MOMENTUM_TREND",
                "strategy_version": "1",
                "universe": "NIFTYDEMO100",
                "start_date": "2024-01-01",
                "end_date": "2025-03-31",
                "adjustment_policy": "ADJUSTED",
            }
        }
    )

    @model_validator(mode="after")
    def validate_request(self) -> PortfolioRunRequest:
        if self.start_date > self.end_date:
            raise ValueError("start_date cannot be after end_date")
        if (self.end_date - self.start_date).days > 3650:
            raise ValueError("Portfolio range cannot exceed 3650 calendar days")
        if self.out_of_sample_start_date is not None and not (
            self.start_date <= self.out_of_sample_start_date <= self.end_date
        ):
            raise ValueError("out_of_sample_start_date must be within the portfolio range")
        self.strategy_code = self.strategy_code.strip().upper()
        self.strategy_version = self.strategy_version.strip()
        self.universe = self.universe.strip()
        self.profile_code = self.profile_code.strip().upper()
        self.profile_version = self.profile_version.strip()
        self.cost_model_code = self.cost_model_code.strip().upper()
        self.cost_model_version = self.cost_model_version.strip()
        self.portfolio_policy_code = self.portfolio_policy_code.strip().upper()
        self.portfolio_policy_version = self.portfolio_policy_version.strip()
        return self


class PortfolioPolicyMetadata(BaseModel):
    policy_code: str
    policy_version: str
    display_name: str
    compatible_profiles: list[str]
    default_initial_capital_inr: Decimal
    default_max_concurrent_positions: int
    default_max_position_weight: Decimal
    default_max_gross_exposure: Decimal
    default_minimum_cash_reserve_pct: Decimal
    allocation_policy: str
    candidate_selection_policy: str
    integer_share_policy: str
    same_security_policy: str
    same_session_event_order: list[str]
    end_policy: str
    mark_to_market_policy: str
    risk_free_rate_convention: str
    leverage_policy: str
    rebalancing_policy: str
    policy_fingerprint: str


class PortfolioMetadataResponse(BaseModel):
    policies: list[PortfolioPolicyMetadata]
    profiles: list[ProfileMetadata]
    cost_models: list[CostModelMetadata]
    strategies: list[StrategyMetadata]
    universes: list[ScannerUniverseMetadata]
    adjustment_policies: list[AdjustmentPolicy]
    parameter_bounds: dict[str, ParameterBounds]
    allocation_semantics: dict[str, str]
    metric_definitions: dict[str, str]
    hard_request_limits: dict[str, int]
    latest_observation_date: date | None
    research_disclaimer: str


class CashLedgerEvent(BaseModel):
    sequence: int
    ledger_event_id: str
    session_date: date
    security_id: UUID | None
    symbol: str | None
    event_type: LedgerEventType
    gross_amount: Decimal
    cost_amount: Decimal
    net_cash_change: Decimal
    resulting_cash_balance: Decimal
    position_id: str | None
    event_fingerprint: str


class RejectedPortfolioCandidate(BaseModel):
    candidate_id: str
    security_id: UUID
    symbol: str
    signal_date: date
    intended_entry_date: date | None
    reason: CandidateRejectionReason
    signal_fingerprint: str
    candidate_fingerprint: str


class PortfolioPosition(BaseModel):
    position_id: str
    security_id: UUID
    symbol: str
    company_name: str
    strategy_code: str
    strategy_version: str
    strategy_fingerprint: str
    signal_date: date
    signal_fingerprint: str
    portfolio_policy_code: str
    portfolio_policy_version: str
    profile_code: str
    profile_version: str
    cost_model_code: str
    cost_model_version: str
    entry_date: date
    entry_raw_price: Decimal
    entry_slipped_price: Decimal
    initial_quantity: int
    current_quantity: Decimal
    entry_turnover: Decimal
    entry_costs: CostBreakdown
    cost_basis: Decimal
    entry_portfolio_weight: Decimal
    stop_price: Decimal | None
    target_price: Decimal | None
    corporate_action_events: list[CorporateActionEffect]
    exit_date: date | None
    exit_raw_price: Decimal | None
    exit_slipped_price: Decimal | None
    exit_reason: PortfolioExitReason
    exit_turnover: Decimal | None
    exit_costs: CostBreakdown | None
    gross_pnl: Decimal | None
    net_pnl: Decimal | None
    net_return: Decimal | None
    portfolio_contribution: Decimal | None
    holding_sessions: int
    warnings: list[str]
    position_fingerprint: str


class DailyPortfolioSnapshot(BaseModel):
    session_date: date
    cash: Decimal
    gross_market_value: Decimal
    portfolio_equity: Decimal
    realized_pnl_to_date: Decimal
    unrealized_pnl: Decimal
    daily_costs: Decimal
    cumulative_costs: Decimal
    open_position_count: int
    gross_exposure_pct: Decimal | None
    cash_pct: Decimal | None
    daily_return: Decimal | None
    drawdown_pct: Decimal | None
    stale_mark_count: int
    warnings: list[str]


class DrawdownDetails(BaseModel):
    max_drawdown_pct: Decimal | None
    max_drawdown_inr: Decimal | None
    peak_date: date | None
    trough_date: date | None
    recovery_date: date | None
    duration_sessions: int | None


class PortfolioMetrics(BaseModel):
    initial_capital: Decimal
    ending_equity: Decimal
    net_portfolio_pnl: Decimal
    total_portfolio_return: Decimal
    cagr: Decimal | None
    annualized_volatility: Decimal | None
    sharpe_ratio: Decimal | None
    sortino_ratio: Decimal | None
    drawdown: DrawdownDetails
    calmar_ratio: Decimal | None
    average_gross_exposure: Decimal | None
    maximum_gross_exposure: Decimal | None
    average_cash_pct: Decimal | None
    minimum_cash: Decimal
    average_open_positions: Decimal | None
    maximum_open_positions: int
    total_traded_turnover: Decimal
    portfolio_turnover: Decimal | None
    total_modeled_transaction_costs: Decimal
    cost_drag: Decimal | None
    entry_count: int
    exit_count: int
    profitable_positions: int
    losing_positions: int
    breakeven_positions: int
    portfolio_win_rate: Decimal | None
    average_realized_position_return: Decimal | None
    median_realized_position_return: Decimal | None
    warnings: list[str]


class PortfolioSegmentMetrics(BaseModel):
    label: Literal["IN_SAMPLE", "OUT_OF_SAMPLE"]
    start_date: date | None
    end_date: date | None
    starting_equity: Decimal | None
    ending_equity: Decimal | None
    total_return: Decimal | None
    cagr: Decimal | None
    annualized_volatility: Decimal | None
    sharpe_ratio: Decimal | None
    sortino_ratio: Decimal | None
    max_drawdown_pct: Decimal | None
    warnings: list[str]


class PortfolioTimings(BaseModel):
    data_load_ms: float
    technical_series_ms: float
    setup_generation_ms: float
    allocation_and_accounting_ms: float
    risk_analytics_ms: float
    response_build_ms: float
    total_service_ms: float


class PortfolioRunResponse(BaseModel):
    normalized_request: PortfolioRunRequest
    strategy: StrategyMetadata
    effective_parameters: dict[str, StrategyScalar]
    profile: ProfileMetadata
    cost_model: CostModelMetadata
    portfolio_policy: PortfolioPolicyMetadata
    universe: ScannerUniverseMetadata
    dataset: DatasetMetadata
    config_fingerprint: str
    dataset_fingerprint: str
    run_fingerprint: str
    historical_sessions_processed: int
    setup_count: int
    accepted_entry_count: int
    rejected_candidate_count: int
    rejected_candidate_reasons: dict[str, int]
    closed_position_count: int
    metrics: PortfolioMetrics
    cost_analytics: AggregateCostBreakdown
    oos_metrics: list[PortfolioSegmentMetrics]
    warnings: list[str]
    daily_equity_curve: list[DailyPortfolioSnapshot]
    total_position_count: int
    returned_position_count: int
    positions_truncated: bool
    position_order: Literal["ENTRY_DATE_SYMBOL_POSITION_ID_ASC"] = "ENTRY_DATE_SYMBOL_POSITION_ID_ASC"
    positions: list[PortfolioPosition]
    total_ledger_event_count: int
    returned_ledger_event_count: int
    ledger_truncated: bool
    ledger_order: Literal["SESSION_SEQUENCE_ASC"] = "SESSION_SEQUENCE_ASC"
    ledger_events: list[CashLedgerEvent]
    returned_rejected_candidate_count: int
    rejected_candidates_truncated: bool
    rejected_candidates: list[RejectedPortfolioCandidate]
    timings: PortfolioTimings
    executed_at: datetime


__all__ = [
    "CashLedgerEvent",
    "DailyPortfolioSnapshot",
    "DrawdownDetails",
    "PortfolioMetadataResponse",
    "PortfolioMetrics",
    "PortfolioPolicyMetadata",
    "PortfolioPosition",
    "PortfolioRunRequest",
    "PortfolioRunResponse",
    "PortfolioSegmentMetrics",
    "PortfolioTimings",
    "RejectedPortfolioCandidate",
]
