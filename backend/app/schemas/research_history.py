from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.backtests import (
    AdjustmentPolicy,
    AggregateCostBreakdown,
    BacktestAnalytics,
    CostModelMetadata,
    DatasetMetadata,
    PeriodBreakdown,
    ProfileMetadata,
    SimulatedTrade,
)
from app.schemas.portfolio import (
    CashLedgerEvent,
    DailyPortfolioSnapshot,
    PortfolioMetrics,
    PortfolioPolicyMetadata,
    PortfolioPosition,
    PortfolioSegmentMetrics,
    RejectedPortfolioCandidate,
)
from app.schemas.research import (
    CompositionComponentConfiguration,
    CompositionEvaluationRequest,
    CompositionPolicyMetadata,
    CompositionStatus,
)
from app.schemas.scanner import ScannerUniverseMetadata


class HistoricalCompositionSourceRequest(BaseModel):
    inline_composition: CompositionEvaluationRequest | None = None
    experiment_id: UUID | None = None

    @model_validator(mode="after")
    def validate_source(self) -> HistoricalCompositionSourceRequest:
        if (self.inline_composition is None) == (self.experiment_id is None):
            raise ValueError("Provide exactly one of inline_composition or experiment_id")
        return self


class HistoricalAnalysisRequest(BaseModel):
    source: HistoricalCompositionSourceRequest
    universe: str = Field(min_length=1, max_length=128)
    start_date: date
    end_date: date
    adjustment_policy: AdjustmentPolicy = "ADJUSTED"
    execution_policy_code: str = "COMPOSITION_NEXT_OPEN_FIXED_HOLD"
    execution_policy_version: str = "1"
    holding_sessions: int = Field(default=20, ge=1, le=252)
    slippage_bps: Decimal = Field(default=Decimal("5"), ge=0, le=1000)
    stop_loss_pct: Decimal | None = Field(default=None, gt=0, lt=1)
    profit_target_pct: Decimal | None = Field(default=None, gt=0, le=10)
    max_exit_delay_sessions: int = Field(default=5, ge=0, le=20)
    cost_model_code: str = "INDIA_NSE_CASH_DELIVERY_2026_09"
    cost_model_version: str = "1"
    brokerage_per_order_inr: Decimal = Field(default=Decimal("0"), ge=0, le=100000)
    brokerage_rate: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("0.1"))
    dp_charge_per_scrip_sell_day_inr: Decimal = Field(default=Decimal("0"), ge=0, le=100000)
    out_of_sample_start_date: date | None = None
    diagnostic_detail_limit: int = Field(default=500, ge=0, le=5000)

    model_config = ConfigDict(allow_inf_nan=False)

    @field_validator("universe")
    @classmethod
    def strip_universe(cls, value: str) -> str:
        return value.strip()

    @field_validator("execution_policy_code", "cost_model_code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("execution_policy_version", "cost_model_version")
    @classmethod
    def strip_version(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_range(self) -> HistoricalAnalysisRequest:
        if self.start_date > self.end_date:
            raise ValueError("start_date cannot be after end_date")
        if (self.end_date - self.start_date).days > 3650:
            raise ValueError("Historical analysis range cannot exceed 3650 calendar days")
        if self.out_of_sample_start_date is not None and not (
            self.start_date <= self.out_of_sample_start_date <= self.end_date
        ):
            raise ValueError("out_of_sample_start_date must be within the analysis range")
        return self


class CompositionBacktestRequest(HistoricalAnalysisRequest):
    trade_notional_inr: Decimal = Field(
        default=Decimal("100000"), ge=1, le=1000000000
    )
    trade_detail_limit: int = Field(default=500, ge=0, le=5000)


class CompositionPortfolioRequest(HistoricalAnalysisRequest):
    portfolio_policy_code: str = "LONG_ONLY_EQUAL_SLOT_PORTFOLIO"
    portfolio_policy_version: str = "1"
    initial_capital_inr: Decimal = Field(default=Decimal("1000000"), gt=0, le=100000000000)
    max_concurrent_positions: int = Field(default=10, ge=1, le=500)
    max_position_weight: Decimal = Field(default=Decimal("0.10"), gt=0, le=1)
    max_gross_exposure: Decimal = Field(default=Decimal("1.00"), gt=0, le=1)
    minimum_cash_reserve_pct: Decimal = Field(default=Decimal("0.00"), ge=0, lt=1)
    risk_free_rate_annual: Decimal = Field(default=Decimal("0.00"), gt=-1, le=10)
    position_detail_limit: int = Field(default=500, ge=0, le=5000)
    ledger_detail_limit: int = Field(default=1000, ge=0, le=10000)

    @field_validator("portfolio_policy_code")
    @classmethod
    def normalize_portfolio_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("portfolio_policy_version")
    @classmethod
    def strip_portfolio_version(cls, value: str) -> str:
        return value.strip()


class HistoricalCompositionSourceMetadata(BaseModel):
    source_type: Literal["INLINE_COMPOSITION", "SAVED_EXPERIMENT"]
    experiment_id: UUID | None
    experiment_name: str | None
    experiment_description: str | None
    composition_config_fingerprint: str
    normalized_composition: CompositionEvaluationRequest
    composition_definition_fields: list[str]
    preserved_point_evaluation_fields: list[str]
    historical_run_fields: list[str]


class CompositionExecutionPolicyMetadata(BaseModel):
    policy_code: str
    policy_version: str
    display_name: str
    backtest_profile_code: str
    backtest_profile_version: str
    signal_timing: str
    entry_timing: str
    exit_timing: str
    direction: str
    overlap_policy: str
    default_holding_sessions: int
    minimum_holding_sessions: int
    maximum_holding_sessions: int
    policy_fingerprint: str


class HistoricalComponentOutcome(BaseModel):
    strategy_code: str
    strategy_version: str
    status: CompositionStatus
    result_fingerprint: str


class HistoricalCompositionOutcome(BaseModel):
    security_id: UUID
    symbol: str
    observation_date: date
    status: CompositionStatus
    matched_strategy_count: int
    insufficient_strategy_count: int
    required_match_count: int
    composition_result_fingerprint: str
    components: list[HistoricalComponentOutcome]


class HistoricalSignalDiagnostics(BaseModel):
    eligible_evaluations: int
    matched_setups: int
    non_matches: int
    insufficient_history: int
    returned_outcomes: int
    outcomes_truncated: bool
    outcome_order: Literal["DATE_SYMBOL_SECURITY_ASC"] = "DATE_SYMBOL_SECURITY_ASC"
    outcomes: list[HistoricalCompositionOutcome]


class HistoricalResearchTimings(BaseModel):
    source_resolution_ms: float
    universe_and_calendar_ms: float
    data_load_ms: float
    feature_generation_ms: float
    composition_evaluation_ms: float
    signal_fingerprint_ms: float
    execution_ms: float
    analytics_ms: float
    response_build_ms: float
    total_service_ms: float


class CompositionBacktestResponse(BaseModel):
    normalized_request: CompositionBacktestRequest
    source: HistoricalCompositionSourceMetadata
    composition_policy: CompositionPolicyMetadata
    components: list[CompositionComponentConfiguration]
    execution_policy: CompositionExecutionPolicyMetadata
    profile: ProfileMetadata
    cost_model: CostModelMetadata
    universe: ScannerUniverseMetadata
    dataset: DatasetMetadata
    historical_signal_fingerprint: str
    historical_dataset_fingerprint: str
    backtest_config_fingerprint: str
    backtest_run_fingerprint: str
    engine_provenance: dict[str, object]
    diagnostics: HistoricalSignalDiagnostics
    skipped_setup_count: int
    skipped_setup_reasons: dict[str, int]
    executed_trade_count: int
    analytics: BacktestAnalytics
    cost_analytics: AggregateCostBreakdown
    yearly_breakdown: list[PeriodBreakdown]
    holdout_breakdown: list[PeriodBreakdown]
    warnings: list[str]
    total_trade_count: int
    returned_trade_count: int
    trades_truncated: bool
    trades: list[SimulatedTrade]
    timings: HistoricalResearchTimings
    executed_at: datetime
    research_disclaimer: str


class CompositionPortfolioResponse(BaseModel):
    normalized_request: CompositionPortfolioRequest
    source: HistoricalCompositionSourceMetadata
    composition_policy: CompositionPolicyMetadata
    components: list[CompositionComponentConfiguration]
    execution_policy: CompositionExecutionPolicyMetadata
    profile: ProfileMetadata
    cost_model: CostModelMetadata
    portfolio_policy: PortfolioPolicyMetadata
    universe: ScannerUniverseMetadata
    dataset: DatasetMetadata
    historical_signal_fingerprint: str
    historical_dataset_fingerprint: str
    portfolio_config_fingerprint: str
    portfolio_run_fingerprint: str
    engine_provenance: dict[str, object]
    diagnostics: HistoricalSignalDiagnostics
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
    positions: list[PortfolioPosition]
    total_ledger_event_count: int
    returned_ledger_event_count: int
    ledger_truncated: bool
    ledger_events: list[CashLedgerEvent]
    returned_rejected_candidate_count: int
    rejected_candidates_truncated: bool
    rejected_candidates: list[RejectedPortfolioCandidate]
    timings: HistoricalResearchTimings
    executed_at: datetime
    research_disclaimer: str


__all__ = [name for name in globals() if name.startswith(("Composition", "Historical"))]
