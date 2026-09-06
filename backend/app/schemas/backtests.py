from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.scanner import ScannerUniverseMetadata
from app.schemas.strategies import StrategyMetadata, StrategyScalar

AdjustmentPolicy = Literal["RAW", "ADJUSTED"]
ExitReason = Literal[
    "TIME_EXIT",
    "STOP_LOSS",
    "PROFIT_TARGET",
    "FORCED_END_OF_TEST",
    "EXIT_PRICE_UNAVAILABLE",
]


class BacktestRunRequest(BaseModel):
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
    trade_notional_inr: Decimal = Field(default=Decimal("100000"), ge=Decimal("1"), le=Decimal("1000000000"))
    slippage_bps: Decimal = Field(default=Decimal("5"), ge=Decimal("0"), le=Decimal("1000"))
    stop_loss_pct: Decimal | None = Field(default=None, gt=Decimal("0"), lt=Decimal("1"))
    profit_target_pct: Decimal | None = Field(default=None, gt=Decimal("0"), le=Decimal("10"))
    max_exit_delay_sessions: int = Field(default=5, ge=0, le=20)
    cost_model_code: str = "INDIA_NSE_CASH_DELIVERY_2026_09"
    cost_model_version: str = "1"
    brokerage_per_order_inr: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), le=Decimal("100000"))
    brokerage_rate: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), le=Decimal("0.1"))
    dp_charge_per_scrip_sell_day_inr: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), le=Decimal("100000"))
    out_of_sample_start_date: date | None = None
    trade_detail_limit: int = Field(default=500, ge=0, le=5000)

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
    def validate_dates(self) -> BacktestRunRequest:
        if self.start_date > self.end_date:
            raise ValueError("start_date cannot be after end_date")
        if (self.end_date - self.start_date).days > 3650:
            raise ValueError("Backtest range cannot exceed 3650 calendar days")
        if self.out_of_sample_start_date is not None and not (
            self.start_date <= self.out_of_sample_start_date <= self.end_date
        ):
            raise ValueError("out_of_sample_start_date must be within the backtest range")
        self.strategy_code = self.strategy_code.strip().upper()
        self.strategy_version = self.strategy_version.strip()
        self.universe = self.universe.strip()
        self.profile_code = self.profile_code.strip().upper()
        self.profile_version = self.profile_version.strip()
        self.cost_model_code = self.cost_model_code.strip().upper()
        self.cost_model_version = self.cost_model_version.strip()
        return self


class ProfileMetadata(BaseModel):
    profile_code: str
    profile_version: str
    display_name: str
    compatible_strategies: list[str]
    default_holding_sessions: dict[str, int]
    entry_timing: str
    exit_timing: str
    default_slippage_bps: Decimal
    default_trade_notional_inr: Decimal
    quantity_policy: str
    stop_policy: str
    target_policy: str
    overlap_policy: str
    end_policy: str
    missing_entry_policy: str
    missing_exit_policy: str
    default_max_exit_delay_sessions: int
    cost_model_code: str
    cost_model_version: str
    profile_fingerprint: str


class CostModelMetadata(BaseModel):
    code: str
    version: str
    display_name: str
    stt_buy_rate: Decimal
    stt_sell_rate: Decimal
    exchange_transaction_rate: Decimal
    sebi_turnover_rate: Decimal
    gst_rate: Decimal
    stamp_duty_buy_rate: Decimal
    gst_taxable_components: list[str]
    monetary_rounding: str
    stt_rounding: str
    default_brokerage_per_order_inr: Decimal
    default_brokerage_rate: Decimal
    default_dp_charge_per_scrip_sell_day_inr: Decimal
    effective_brokerage_per_order_inr: Decimal
    effective_brokerage_rate: Decimal
    effective_dp_charge_per_scrip_sell_day_inr: Decimal
    cost_model_fingerprint: str
    broker_specific_costs_excluded_by_default: bool = True


class ParameterBounds(BaseModel):
    minimum: Decimal | int
    maximum: Decimal | int
    default: Decimal | int | None


class BacktestMetadataResponse(BaseModel):
    profiles: list[ProfileMetadata]
    cost_models: list[CostModelMetadata]
    strategies: list[StrategyMetadata]
    universes: list[ScannerUniverseMetadata]
    adjustment_policies: list[AdjustmentPolicy]
    parameter_bounds: dict[str, ParameterBounds]
    overlap_policies: list[str]
    end_policies: list[str]
    sample_size_warning_rules: dict[str, int]
    hard_request_limits: dict[str, int]
    latest_observation_date: date | None
    research_disclaimer: str


class CostBreakdown(BaseModel):
    turnover: Decimal
    brokerage: Decimal
    stt: Decimal
    exchange_transaction_charge: Decimal
    sebi_charge: Decimal
    gst: Decimal
    stamp_duty: Decimal
    dp_charge: Decimal
    total_charges: Decimal


class CorporateActionEffect(BaseModel):
    action_id: UUID
    action_type: str
    ex_date: date
    ratio_numerator: Decimal | None
    ratio_denominator: Decimal | None
    quantity_before: Decimal
    quantity_after: Decimal
    reference_factor: Decimal


class SimulatedTrade(BaseModel):
    trade_id: str
    security_id: UUID
    symbol: str
    company_name: str
    strategy_code: str
    strategy_version: str
    strategy_fingerprint: str
    profile_code: str
    profile_version: str
    adjustment_policy: AdjustmentPolicy
    signal_date: date
    signal_decision_at: datetime
    signal_result_fingerprint: str
    entry_date: date
    raw_entry_price: Decimal
    slipped_entry_price: Decimal
    entry_quantity: int
    quantity: Decimal
    deployed_notional: Decimal
    entry_cost: CostBreakdown
    stop_price: Decimal | None
    target_price: Decimal | None
    exit_date: date | None
    raw_exit_price: Decimal | None
    slipped_exit_price: Decimal | None
    exit_reason: ExitReason
    exit_cost: CostBreakdown | None
    holding_sessions: int
    gross_pnl: Decimal | None
    total_costs: Decimal | None
    net_pnl: Decimal | None
    gross_return_pct: Decimal | None
    net_return_pct: Decimal | None
    maximum_favorable_excursion: Decimal | None
    maximum_adverse_excursion: Decimal | None
    corporate_action_events: list[CorporateActionEffect]
    warnings: list[str]
    trade_fingerprint: str


class BacktestAnalytics(BaseModel):
    signal_count: int
    executable_setup_count: int
    executed_trade_count: int
    closed_trade_count: int
    forced_end_count: int
    closed_normal_count: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    win_rate: Decimal | None
    loss_rate: Decimal | None
    average_gross_return: Decimal | None
    average_net_return: Decimal | None
    median_net_return: Decimal | None
    average_gross_pnl: Decimal | None
    average_net_pnl: Decimal | None
    total_gross_pnl: Decimal
    total_net_pnl: Decimal
    total_transaction_costs: Decimal
    cost_drag_pct: Decimal | None
    average_transaction_cost_per_trade: Decimal | None
    profit_factor: Decimal | None
    expectancy_per_trade: Decimal | None
    best_trade_return: Decimal | None
    worst_trade_return: Decimal | None
    standard_deviation_net_returns: Decimal | None
    percentile_05: Decimal | None
    percentile_25: Decimal | None
    percentile_50: Decimal | None
    percentile_75: Decimal | None
    percentile_95: Decimal | None
    average_holding_sessions: Decimal | None
    maximum_holding_sessions: int | None
    average_mae: Decimal | None
    average_mfe: Decimal | None
    warnings: list[str]


class PeriodBreakdown(BaseModel):
    label: str
    setup_count: int
    trade_count: int
    average_net_return: Decimal | None
    median_net_return: Decimal | None
    win_rate: Decimal | None
    profit_factor: Decimal | None
    total_net_pnl: Decimal
    total_costs: Decimal


class AggregateCostBreakdown(BaseModel):
    stt: Decimal
    exchange_transaction_charge: Decimal
    sebi_charge: Decimal
    gst: Decimal
    stamp_duty: Decimal
    brokerage: Decimal
    dp_charge: Decimal
    total: Decimal
    broker_specific_costs_excluded: bool


class DatasetMetadata(BaseModel):
    dataset_code: str
    dataset_version: str
    provider: str
    security_count: int
    price_row_count: int
    corporate_action_revision_count: int
    membership_interval_count: int
    trading_session_count: int


class BacktestTimings(BaseModel):
    data_load_ms: float
    feature_series_ms: float
    condition_evaluation_ms: float
    trade_simulation_ms: float
    cost_and_analytics_ms: float
    response_build_ms: float
    total_service_ms: float


class BacktestRunResponse(BaseModel):
    normalized_request: BacktestRunRequest
    strategy: StrategyMetadata
    effective_parameters: dict[str, StrategyScalar]
    profile: ProfileMetadata
    cost_model: CostModelMetadata
    universe: ScannerUniverseMetadata
    dataset: DatasetMetadata
    config_fingerprint: str
    dataset_fingerprint: str
    run_fingerprint: str
    historical_sessions_processed: int
    setup_count: int
    executable_setup_count: int
    executed_trade_count: int
    skipped_setup_count: int
    skipped_setup_reasons: dict[str, int]
    analytics: BacktestAnalytics
    cost_analytics: AggregateCostBreakdown
    yearly_breakdown: list[PeriodBreakdown]
    holdout_breakdown: list[PeriodBreakdown]
    warnings: list[str]
    total_trade_count: int
    returned_trade_count: int
    trades_truncated: bool
    trade_order: Literal["ENTRY_DATE_SYMBOL_TRADE_ID_ASC"] = "ENTRY_DATE_SYMBOL_TRADE_ID_ASC"
    trades: list[SimulatedTrade]
    timings: BacktestTimings
    executed_at: datetime
