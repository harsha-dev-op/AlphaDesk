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
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    isin: Mapped[str | None] = mapped_column(String(12), unique=True)
    security_type: Mapped[str] = mapped_column(String(32), default="EQUITY", nullable=False)
    sector: Mapped[str | None] = mapped_column(String(128))
    industry: Mapped[str | None] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    listing_date: Mapped[date | None] = mapped_column(Date)
    delisting_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

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
    __table_args__ = (Index("ix_data_ingestion_runs_status_completed", "status", "completed_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    dataset_code: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    records_written: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(Text)
