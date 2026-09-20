"""fundamental and sector intelligence

Revision ID: d4a13f0c2b71
Revises: b31c9d7e4f20
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4a13f0c2b71"
down_revision: str | None = "b31c9d7e4f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fundamental_filings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("security_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_filing_id", sa.String(160)),
        sa.Column("filing_type", sa.String(64), nullable=False),
        sa.Column("reporting_frequency", sa.String(16), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_quarter", sa.Integer()),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("audit_status", sa.String(16), nullable=False),
        sa.Column("submission_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision_status", sa.String(16), nullable=False),
        sa.Column("supersedes_filing_id", sa.Uuid()),
        sa.Column("source_artifact_id", sa.Uuid(), nullable=False),
        sa.Column("ingestion_run_id", sa.Uuid(), nullable=False),
        sa.Column("parser_version", sa.String(32), nullable=False),
        sa.Column("normalized_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("period_end >= period_start", name="valid_fundamental_filing_period"),
        sa.CheckConstraint("scope IN ('CONSOLIDATED', 'STANDALONE')", name="valid_fundamental_scope"),
        sa.CheckConstraint("reporting_frequency IN ('QUARTERLY', 'ANNUAL')", name="valid_fundamental_frequency"),
        sa.CheckConstraint("audit_status IN ('AUDITED', 'UNAUDITED', 'UNKNOWN')", name="valid_fundamental_audit_status"),
        sa.CheckConstraint("revision_status IN ('ORIGINAL', 'REVISED')", name="valid_fundamental_revision_status"),
        sa.CheckConstraint("supersedes_filing_id IS NULL OR supersedes_filing_id <> id", name="fundamental_filing_not_self_superseding"),
        sa.ForeignKeyConstraint(["security_id"], ["securities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supersedes_filing_id"], ["fundamental_filings.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_artifact_id"], ["source_artifacts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["data_ingestion_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "normalized_fingerprint", name="uq_fundamental_filings_source_fingerprint"),
        sa.UniqueConstraint("supersedes_filing_id", name="uq_fundamental_filings_supersedes"),
    )
    op.create_index("ix_fundamental_filings_security_available", "fundamental_filings", ["security_id", "available_at"])
    op.create_index("ix_fundamental_filings_period_scope", "fundamental_filings", ["security_id", "period_end", "scope"])

    op.create_table(
        "fundamental_facts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("filing_id", sa.Uuid(), nullable=False),
        sa.Column("normalized_concept", sa.String(64)),
        sa.Column("source_concept", sa.String(255), nullable=False),
        sa.Column("value", sa.Numeric(30, 8)),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("scale", sa.Integer(), server_default="0", nullable=False),
        sa.Column("fact_kind", sa.String(16), nullable=False),
        sa.Column("value_nature", sa.String(16), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("fact_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("fact_kind IN ('DURATION', 'INSTANT')", name="valid_fundamental_fact_kind"),
        sa.CheckConstraint("value_nature IN ('QUARTERLY', 'YTD', 'ANNUAL', 'INSTANT')", name="valid_fundamental_value_nature"),
        sa.CheckConstraint("period_end >= period_start", name="valid_fundamental_fact_period"),
        sa.ForeignKeyConstraint(["filing_id"], ["fundamental_filings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("filing_id", "source_concept", "period_start", "period_end", name="uq_fundamental_fact_identity"),
    )
    op.create_index("ix_fundamental_facts_filing_concept", "fundamental_facts", ["filing_id", "normalized_concept"])

    op.create_table(
        "security_industry_classifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("security_id", sa.Uuid(), nullable=False),
        sa.Column("macro_economic_sector", sa.String(160), nullable=False),
        sa.Column("sector", sa.String(160), nullable=False),
        sa.Column("industry", sa.String(160), nullable=False),
        sa.Column("basic_industry", sa.String(160), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_artifact_id", sa.Uuid(), nullable=False),
        sa.Column("ingestion_run_id", sa.Uuid(), nullable=False),
        sa.Column("parser_version", sa.String(32), nullable=False),
        sa.Column("normalized_fingerprint", sa.String(64), nullable=False),
        sa.Column("sector_benchmark_index_id", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["security_id"], ["securities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_artifact_id"], ["source_artifacts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["data_ingestion_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["sector_benchmark_index_id"], ["indices.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("security_id", "snapshot_date", "normalized_fingerprint", name="uq_security_classification_snapshot"),
    )
    op.create_index("ix_security_classifications_as_of", "security_industry_classifications", ["security_id", "snapshot_date", "available_at"])
    op.create_index("ix_security_classifications_peer", "security_industry_classifications", ["sector", "industry", "basic_industry"])


def downgrade() -> None:
    op.drop_index("ix_security_classifications_peer", table_name="security_industry_classifications")
    op.drop_index("ix_security_classifications_as_of", table_name="security_industry_classifications")
    op.drop_table("security_industry_classifications")
    op.drop_index("ix_fundamental_facts_filing_concept", table_name="fundamental_facts")
    op.drop_table("fundamental_facts")
    op.drop_index("ix_fundamental_filings_period_scope", table_name="fundamental_filings")
    op.drop_index("ix_fundamental_filings_security_available", table_name="fundamental_filings")
    op.drop_table("fundamental_filings")
