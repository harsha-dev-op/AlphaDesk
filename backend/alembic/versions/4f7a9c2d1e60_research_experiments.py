"""research_experiments

Revision ID: 4f7a9c2d1e60
Revises: c9a4d7e2f631
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "4f7a9c2d1e60"
down_revision: str | None = "c9a4d7e2f631"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_experiments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("policy_code", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("normalized_request", sa.JSON(), nullable=False),
        sa.Column("config_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_experiments")),
    )
    op.create_index(
        "ix_research_experiments_config_fingerprint",
        "research_experiments",
        ["config_fingerprint"],
        unique=False,
    )
    op.create_index(
        "ix_research_experiments_created_at_id",
        "research_experiments",
        ["created_at", "id"],
        unique=False,
    )

    op.create_table(
        "research_experiment_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replay_status", sa.String(length=48), nullable=False),
        sa.Column("reference_run_id", sa.Uuid(), nullable=True),
        sa.Column("normalized_request", sa.JSON(), nullable=False),
        sa.Column("composition_config_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("dataset_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("run_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("engine_provenance", sa.JSON(), nullable=False),
        sa.Column("result_summary", sa.JSON(), nullable=False),
        sa.Column("member_outcomes", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "replay_status IN ('INITIAL', 'REPRODUCED', 'DATASET_DRIFT_DETECTED', "
            "'ENGINE_OR_RESULT_DRIFT_DETECTED')",
            name=op.f("ck_research_experiment_runs_valid_replay_status"),
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["research_experiments.id"],
            name=op.f("fk_research_experiment_runs_experiment_id_research_experiments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reference_run_id"],
            ["research_experiment_runs.id"],
            name=op.f("fk_research_experiment_runs_reference_run_id_research_experiment_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_research_experiment_runs")),
    )
    op.create_index(
        "ix_research_experiment_runs_dataset_fingerprint",
        "research_experiment_runs",
        ["dataset_fingerprint"],
        unique=False,
    )
    op.create_index(
        "ix_research_experiment_runs_experiment_executed_id",
        "research_experiment_runs",
        ["experiment_id", "executed_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_research_experiment_runs_run_fingerprint",
        "research_experiment_runs",
        ["run_fingerprint"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_research_experiment_runs_run_fingerprint",
        table_name="research_experiment_runs",
    )
    op.drop_index(
        "ix_research_experiment_runs_experiment_executed_id",
        table_name="research_experiment_runs",
    )
    op.drop_index(
        "ix_research_experiment_runs_dataset_fingerprint",
        table_name="research_experiment_runs",
    )
    op.drop_table("research_experiment_runs")
    op.drop_index(
        "ix_research_experiments_created_at_id", table_name="research_experiments"
    )
    op.drop_index(
        "ix_research_experiments_config_fingerprint", table_name="research_experiments"
    )
    op.drop_table("research_experiments")
