"""nse_source_provenance

Revision ID: 7c2e1b8d4a90
Revises: 4f7a9c2d1e60
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "7c2e1b8d4a90"
down_revision: str | None = "4f7a9c2d1e60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("data_ingestion_runs") as batch_op:
        batch_op.add_column(sa.Column("requested_start", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("requested_end", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("parser_version", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column("inserted_count", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("unchanged_count", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("rejected_count", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("conflict_count", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("warning_count", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("dry_run", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
        batch_op.create_check_constraint(
            "ck_data_ingestion_runs_valid_ingestion_status",
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'SUCCESS', 'PARTIAL', 'FAILED')",
        )

    op.create_table(
        "source_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ingestion_run_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("artifact_type", sa.String(length=48), nullable=False),
        sa.Column("source_date", sa.Date(), nullable=False),
        sa.Column("original_file_name", sa.String(length=255), nullable=False),
        sa.Column("source_locator", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("parser_code", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=32), nullable=False),
        sa.Column("source_schema_version", sa.String(length=32), nullable=True),
        sa.Column("normalized_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("parse_status", sa.String(length=16), nullable=False),
        sa.Column("row_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("accepted_row_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rejected_row_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("warning_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("storage_key", sa.String(length=255), nullable=True),
        sa.Column("artifact_metadata", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "parse_status IN ('SUCCEEDED', 'PARTIAL', 'FAILED', 'CONFLICT')",
            name=op.f("ck_source_artifacts_valid_artifact_parse_status"),
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["data_ingestion_runs.id"],
            name=op.f("fk_source_artifacts_ingestion_run_id_data_ingestion_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_artifacts")),
        sa.UniqueConstraint(
            "provider", "artifact_type", "source_date", "sha256",
            name="uq_source_artifacts_identity",
        ),
    )
    op.create_index(
        "ix_source_artifacts_imported_at", "source_artifacts", ["imported_at"], unique=False
    )
    op.create_index(
        "ix_source_artifacts_type_date",
        "source_artifacts",
        ["artifact_type", "source_date"],
        unique=False,
    )

    op.create_table(
        "ingestion_issues",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_artifact_id", sa.Uuid(), nullable=False),
        sa.Column("ingestion_run_id", sa.Uuid(), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("row_key", sa.String(length=160), nullable=True),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("issue_metadata", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "severity IN ('ERROR', 'WARNING', 'INFO')",
            name=op.f("ck_ingestion_issues_valid_issue_severity"),
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["data_ingestion_runs.id"],
            name=op.f("fk_ingestion_issues_ingestion_run_id_data_ingestion_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_artifact_id"],
            ["source_artifacts.id"],
            name=op.f("fk_ingestion_issues_source_artifact_id_source_artifacts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingestion_issues")),
    )
    op.create_index(
        "ix_ingestion_issues_artifact_severity",
        "ingestion_issues",
        ["source_artifact_id", "severity"],
        unique=False,
    )
    op.create_index(
        "ix_ingestion_issues_code_created",
        "ingestion_issues",
        ["code", "created_at"],
        unique=False,
    )

    _add_lineage_columns()


def _add_lineage_columns() -> None:
    lineage_tables = ("securities", "daily_prices", "corporate_actions", "index_memberships")
    for table_name in lineage_tables:
        with op.batch_alter_table(table_name) as batch_op:
            if table_name == "securities":
                batch_op.add_column(sa.Column("series", sa.String(length=8), nullable=True))
            batch_op.add_column(
                sa.Column("data_origin", sa.String(length=32), server_default="UNKNOWN", nullable=False)
            )
            batch_op.add_column(sa.Column("source_artifact_id", sa.Uuid(), nullable=True))
            batch_op.add_column(sa.Column("ingestion_run_id", sa.Uuid(), nullable=True))
            batch_op.create_foreign_key(
                f"fk_{table_name}_source_artifact_id_source_artifacts",
                "source_artifacts",
                ["source_artifact_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch_op.create_foreign_key(
                f"fk_{table_name}_ingestion_run_id_data_ingestion_runs",
                "data_ingestion_runs",
                ["ingestion_run_id"],
                ["id"],
                ondelete="RESTRICT",
            )

    with op.batch_alter_table("trading_calendar") as batch_op:
        batch_op.add_column(
            sa.Column("data_origin", sa.String(length=32), server_default="UNKNOWN", nullable=False)
        )
        batch_op.add_column(sa.Column("source_artifact_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("ingestion_run_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_trading_calendar_source_artifact_id_source_artifacts",
            "source_artifacts",
            ["source_artifact_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_trading_calendar_ingestion_run_id_data_ingestion_runs",
            "data_ingestion_runs",
            ["ingestion_run_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "UPDATE securities SET data_origin = 'DEMO' "
            "WHERE isin LIKE 'DEMO%' AND company_name LIKE '% Demo %'"
        )
        for table_name in ("daily_prices", "corporate_actions", "index_memberships"):
            op.execute(
                f"UPDATE {table_name} SET data_origin = 'DEMO' WHERE source = 'DEMO_LOCAL'"
            )
        op.execute(
            "UPDATE trading_calendar SET data_origin = 'DEMO' "
            "WHERE notes = 'Weekend' OR notes LIKE 'Demo %'"
        )


def downgrade() -> None:
    for table_name in (
        "trading_calendar",
        "index_memberships",
        "corporate_actions",
        "daily_prices",
        "securities",
    ):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_constraint(
                f"fk_{table_name}_ingestion_run_id_data_ingestion_runs", type_="foreignkey"
            )
            batch_op.drop_constraint(
                f"fk_{table_name}_source_artifact_id_source_artifacts", type_="foreignkey"
            )
            batch_op.drop_column("ingestion_run_id")
            batch_op.drop_column("source_artifact_id")
            batch_op.drop_column("data_origin")
            if table_name == "securities":
                batch_op.drop_column("series")

    op.drop_index("ix_ingestion_issues_code_created", table_name="ingestion_issues")
    op.drop_index("ix_ingestion_issues_artifact_severity", table_name="ingestion_issues")
    op.drop_table("ingestion_issues")
    op.drop_index("ix_source_artifacts_type_date", table_name="source_artifacts")
    op.drop_index("ix_source_artifacts_imported_at", table_name="source_artifacts")
    op.drop_table("source_artifacts")

    with op.batch_alter_table("data_ingestion_runs") as batch_op:
        batch_op.drop_constraint("ck_data_ingestion_runs_valid_ingestion_status", type_="check")
        batch_op.drop_column("dry_run")
        batch_op.drop_column("warning_count")
        batch_op.drop_column("conflict_count")
        batch_op.drop_column("rejected_count")
        batch_op.drop_column("unchanged_count")
        batch_op.drop_column("inserted_count")
        batch_op.drop_column("parser_version")
        batch_op.drop_column("requested_end")
        batch_op.drop_column("requested_start")
