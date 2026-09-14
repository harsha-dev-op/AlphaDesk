from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy import event
from sqlalchemy.orm import Mapped, Mapper, mapped_column, relationship

from app.database.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Security(TimestampMixin, Base):
    __tablename__ = "securities"
    __table_args__ = (
        UniqueConstraint("exchange", "symbol", name="uq_securities_exchange_symbol"),
        Index("ix_securities_symbol", "symbol"),
        Index("ix_securities_active_exchange", "is_active", "exchange"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    trading_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    series: Mapped[str | None] = mapped_column(String(8))
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    isin: Mapped[str | None] = mapped_column(String(12), unique=True)
    security_type: Mapped[str] = mapped_column(String(32), default="EQUITY", nullable=False)
    sector: Mapped[str | None] = mapped_column(String(128))
    industry: Mapped[str | None] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    listing_date: Mapped[date | None] = mapped_column(Date)
    delisting_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    data_origin: Mapped[str] = mapped_column(String(32), default="UNKNOWN", nullable=False)
    source_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_artifacts.id", ondelete="RESTRICT")
    )
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_ingestion_runs.id", ondelete="RESTRICT")
    )

    prices: Mapped[list[DailyPrice]] = relationship(back_populates="security", cascade="all, delete-orphan")
    corporate_actions: Mapped[list[CorporateAction]] = relationship(back_populates="security", cascade="all, delete-orphan")


class DailyPrice(Base):
    __tablename__ = "daily_prices"
    __table_args__ = (
        UniqueConstraint("security_id", "trading_date", name="uq_daily_prices_security_date"),
        CheckConstraint("open >= 0 AND high >= 0 AND low >= 0 AND close >= 0", name="nonnegative_prices"),
        CheckConstraint("high >= low", name="high_gte_low"),
        CheckConstraint("open >= low AND open <= high", name="open_within_range"),
        CheckConstraint("close >= low AND close <= high", name="close_within_range"),
        CheckConstraint("volume >= 0", name="nonnegative_volume"),
        Index("ix_daily_prices_security_date", "security_id", "trading_date"),
        Index("ix_daily_prices_trading_date", "trading_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    security_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"), nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False)
    traded_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    data_origin: Mapped[str] = mapped_column(String(32), default="UNKNOWN", nullable=False)
    source_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_artifacts.id", ondelete="RESTRICT")
    )
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_ingestion_runs.id", ondelete="RESTRICT")
    )

    security: Mapped[Security] = relationship(back_populates="prices")


class CorporateAction(Base):
    __tablename__ = "corporate_actions"
    __table_args__ = (
        CheckConstraint("ratio_numerator IS NULL OR ratio_numerator > 0", name="positive_ratio_numerator"),
        CheckConstraint("ratio_denominator IS NULL OR ratio_denominator > 0", name="positive_ratio_denominator"),
        CheckConstraint("supersedes_action_id IS NULL OR supersedes_action_id <> id", name="corporate_action_not_self_superseding"),
        UniqueConstraint("supersedes_action_id", name="uq_corporate_actions_supersedes_action_id"),
        Index("ix_corporate_actions_security_ex_date", "security_id", "ex_date"),
        Index("ix_corporate_actions_security_available_at", "security_id", "available_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    security_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"), nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    announcement_date: Mapped[date | None] = mapped_column(Date)
    ex_date: Mapped[date] = mapped_column(Date, nullable=False)
    record_date: Mapped[date | None] = mapped_column(Date)
    ratio_numerator: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    ratio_denominator: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    cash_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    supersedes_action_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("corporate_actions.id", ondelete="RESTRICT")
    )
    data_origin: Mapped[str] = mapped_column(String(32), default="UNKNOWN", nullable=False)
    source_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_artifacts.id", ondelete="RESTRICT")
    )
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_ingestion_runs.id", ondelete="RESTRICT")
    )

    security: Mapped[Security] = relationship(back_populates="corporate_actions")


@event.listens_for(CorporateAction, "before_insert")
def _set_corporate_action_availability(
    _mapper: Mapper[CorporateAction],
    _connection: object,
    target: CorporateAction,
) -> None:
    """Make local receipt time the default knowledge boundary for new actions."""
    if target.ingested_at is None:
        target.ingested_at = datetime.now(UTC)
    if target.available_at is None:
        target.available_at = target.source_published_at or target.ingested_at
    if target.source_published_at is not None and target.available_at != target.source_published_at:
        raise ValueError("available_at must equal source_published_at when a source timestamp is provided")


class MarketIndex(TimestampMixin, Base):
    __tablename__ = "indices"
    __table_args__ = (UniqueConstraint("provider", "symbol", name="uq_indices_provider_symbol"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    memberships: Mapped[list[IndexMembership]] = relationship(back_populates="index", cascade="all, delete-orphan")


class IndexMembership(Base):
    __tablename__ = "index_memberships"
    __table_args__ = (
        UniqueConstraint("index_id", "security_id", "valid_from", name="uq_index_memberships_interval"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="valid_membership_range"),
        Index("ix_index_memberships_as_of", "index_id", "valid_from", "valid_to"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    index_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("indices.id", ondelete="CASCADE"), nullable=False)
    security_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    data_origin: Mapped[str] = mapped_column(String(32), default="UNKNOWN", nullable=False)
    source_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_artifacts.id", ondelete="RESTRICT")
    )
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_ingestion_runs.id", ondelete="RESTRICT")
    )
    index: Mapped[MarketIndex] = relationship(back_populates="memberships")
    security: Mapped[Security] = relationship()


class FundamentalReport(Base):
    __tablename__ = "fundamental_reports"
    __table_args__ = (
        UniqueConstraint("security_id", "fiscal_period_end", "reported_date", "source", name="uq_fundamentals_report_version"),
        CheckConstraint("fiscal_period_end >= fiscal_period_start", name="valid_fiscal_period"),
        Index("ix_fundamentals_point_in_time", "security_id", "effective_date", "fiscal_period_end"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    security_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"), nullable=False)
    fiscal_period_start: Mapped[date] = mapped_column(Date, nullable=False)
    fiscal_period_end: Mapped[date] = mapped_column(Date, nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_quarter: Mapped[int | None] = mapped_column(Integer)
    reported_date: Mapped[date] = mapped_column(Date, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False, comment="First date this version was publicly knowable")
    revenue: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    ebitda: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    operating_profit: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    net_profit: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    eps: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    total_assets: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    total_debt: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    cash: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    equity: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    security: Mapped[Security] = relationship()


class TradingCalendar(Base):
    __tablename__ = "trading_calendar"
    __table_args__ = (
        UniqueConstraint("exchange", "trading_date", name="uq_trading_calendar_exchange_date"),
        Index("ix_trading_calendar_lookup", "exchange", "is_trading_day", "trading_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_trading_day: Mapped[bool] = mapped_column(Boolean, nullable=False)
    session_open: Mapped[time | None] = mapped_column(Time)
    session_close: Mapped[time | None] = mapped_column(Time)
    session_type: Mapped[str] = mapped_column(String(32), default="REGULAR", nullable=False)
    notes: Mapped[str | None] = mapped_column(String(255))
    data_origin: Mapped[str] = mapped_column(String(32), default="UNKNOWN", nullable=False)
    source_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_artifacts.id", ondelete="RESTRICT")
    )
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_ingestion_runs.id", ondelete="RESTRICT")
    )


class StrategyDefinition(TimestampMixin, Base):
    __tablename__ = "strategy_definitions"
    __table_args__ = (UniqueConstraint("strategy_code", "version", name="uq_strategy_definitions_code_version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    strategy_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class DataIngestionRun(Base):
    __tablename__ = "data_ingestion_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'SUCCESS', 'PARTIAL', 'FAILED')",
            name="valid_ingestion_status",
        ),
        Index("ix_data_ingestion_runs_status_completed", "status", "completed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    dataset_code: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    records_written: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(Text)
    requested_start: Mapped[date | None] = mapped_column(Date)
    requested_end: Mapped[date | None] = mapped_column(Date)
    parser_version: Mapped[str | None] = mapped_column(String(32))
    inserted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conflict_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SourceArtifact(Base):
    """Checksum-addressed metadata for one official/public source file."""

    __tablename__ = "source_artifacts"
    __table_args__ = (
        CheckConstraint(
            "parse_status IN ('SUCCEEDED', 'PARTIAL', 'FAILED', 'CONFLICT')",
            name="valid_artifact_parse_status",
        ),
        UniqueConstraint(
            "provider", "artifact_type", "source_date", "sha256",
            name="uq_source_artifacts_identity",
        ),
        Index("ix_source_artifacts_type_date", "artifact_type", "source_date"),
        Index("ix_source_artifacts_imported_at", "imported_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_ingestion_runs.id", ondelete="RESTRICT")
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(48), nullable=False)
    source_date: Mapped[date] = mapped_column(Date, nullable=False)
    original_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_locator: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    parser_code: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source_schema_version: Mapped[str | None] = mapped_column(String(32))
    normalized_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    parse_status: Mapped[str] = mapped_column(String(16), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    accepted_row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejected_row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(Text)
    storage_key: Mapped[str | None] = mapped_column(String(255))
    artifact_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)


class IngestionIssue(Base):
    """Bounded structured rejection, conflict, or warning tied to an artifact."""

    __tablename__ = "ingestion_issues"
    __table_args__ = (
        CheckConstraint("severity IN ('ERROR', 'WARNING', 'INFO')", name="valid_issue_severity"),
        Index("ix_ingestion_issues_artifact_severity", "source_artifact_id", "severity"),
        Index("ix_ingestion_issues_code_created", "code", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_artifacts.id", ondelete="RESTRICT"), nullable=False
    )
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_ingestion_runs.id", ondelete="RESTRICT")
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    row_number: Mapped[int | None] = mapped_column(Integer)
    row_key: Mapped[str | None] = mapped_column(String(160))
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    issue_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ResearchExperiment(Base):
    """Immutable saved behavior configuration for a research composition."""

    __tablename__ = "research_experiments"
    __table_args__ = (
        Index("ix_research_experiments_created_at_id", "created_at", "id"),
        Index("ix_research_experiments_config_fingerprint", "config_fingerprint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    policy_code: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_request: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ResearchExperimentRun(Base):
    """Append-only execution provenance for an immutable experiment."""

    __tablename__ = "research_experiment_runs"
    __table_args__ = (
        CheckConstraint(
            "replay_status IN ('INITIAL', 'REPRODUCED', 'DATASET_DRIFT_DETECTED', "
            "'ENGINE_OR_RESULT_DRIFT_DETECTED')",
            name="valid_replay_status",
        ),
        Index(
            "ix_research_experiment_runs_experiment_executed_id",
            "experiment_id",
            "executed_at",
            "id",
        ),
        Index("ix_research_experiment_runs_run_fingerprint", "run_fingerprint"),
        Index("ix_research_experiment_runs_dataset_fingerprint", "dataset_fingerprint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_experiments.id", ondelete="RESTRICT"), nullable=False
    )
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    replay_status: Mapped[str] = mapped_column(String(48), nullable=False)
    reference_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_experiment_runs.id", ondelete="RESTRICT")
    )
    normalized_request: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    composition_config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    run_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    engine_provenance: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    result_summary: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    member_outcomes: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False)


def _reject_immutable_write(_mapper: Mapper[object], _connection: object, _target: object) -> None:
    raise ValueError("Saved research experiment provenance is immutable and append-only")


for _immutable_model in (ResearchExperiment, ResearchExperimentRun):
    event.listen(_immutable_model, "before_update", _reject_immutable_write)
    event.listen(_immutable_model, "before_delete", _reject_immutable_write)
