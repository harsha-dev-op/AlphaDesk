"""index_daily_prices

Revision ID: b31c9d7e4f20
Revises: 7c2e1b8d4a90
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "b31c9d7e4f20"
down_revision: str | None = "7c2e1b8d4a90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "index_daily_prices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("index_id", sa.Uuid(), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("high", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("low", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("close", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("source_mode", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("ingestion_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_mode IN ('DEMO', 'OFFICIAL')",
            name=op.f("ck_index_daily_prices_valid_index_price_source_mode"),
        ),
        sa.CheckConstraint(
            "open > 0 AND high > 0 AND low > 0 AND close > 0",
            name=op.f("ck_index_daily_prices_nonnegative_index_prices"),
        ),
        sa.CheckConstraint(
            "high >= low",
            name=op.f("ck_index_daily_prices_index_price_high_gte_low"),
        ),
        sa.CheckConstraint(
            "open >= low AND open <= high",
            name=op.f("ck_index_daily_prices_index_price_open_within_range"),
        ),
        sa.CheckConstraint(
            "close >= low AND close <= high",
            name=op.f("ck_index_daily_prices_index_price_close_within_range"),
        ),
        sa.ForeignKeyConstraint(
            ["index_id"],
            ["indices.id"],
            name=op.f("fk_index_daily_prices_index_id_indices"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_artifact_id"],
            ["source_artifacts.id"],
            name=op.f("fk_index_daily_prices_source_artifact_id_source_artifacts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["data_ingestion_runs.id"],
            name=op.f("fk_index_daily_prices_ingestion_run_id_data_ingestion_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_index_daily_prices")),
        sa.UniqueConstraint(
            "index_id",
            "trading_date",
            "source_mode",
            name="uq_index_daily_prices_index_date_source",
        ),
    )
    op.create_index(
        "ix_index_daily_prices_index_source_date",
        "index_daily_prices",
        ["index_id", "source_mode", "trading_date"],
        unique=False,
    )
    op.create_index(
        "ix_index_daily_prices_available_at",
        "index_daily_prices",
        ["index_id", "source_mode", "available_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_index_daily_prices_available_at", table_name="index_daily_prices"
    )
    op.drop_index(
        "ix_index_daily_prices_index_source_date", table_name="index_daily_prices"
    )
    op.drop_table("index_daily_prices")
