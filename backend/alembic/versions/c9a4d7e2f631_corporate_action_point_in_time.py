"""corporate_action_point_in_time

Revision ID: c9a4d7e2f631
Revises: 8b85071c8e5c
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c9a4d7e2f631"
down_revision: str | None = "8b85071c8e5c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("corporate_actions") as batch_op:
        batch_op.add_column(sa.Column("source_published_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column(
                "available_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("supersedes_action_id", sa.Uuid(), nullable=True))

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                UPDATE corporate_actions
                SET available_at = COALESCE(
                    source_published_at,
                    CASE
                        WHEN announcement_date IS NOT NULL
                        THEN (announcement_date::timestamp + time '23:59:59')
                             AT TIME ZONE 'Asia/Kolkata'
                        ELSE ingested_at
                    END
                )
                """
            )
        )
    else:
        op.execute(
            sa.text(
                """
                UPDATE corporate_actions
                SET available_at = COALESCE(
                    source_published_at,
                    CASE
                        WHEN announcement_date IS NOT NULL
                        THEN datetime(announcement_date, '+23 hours', '+59 minutes', '+59 seconds')
                        ELSE ingested_at
                    END
                )
                """
            )
        )

    with op.batch_alter_table("corporate_actions") as batch_op:
        batch_op.alter_column("available_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch_op.create_check_constraint(
            "ck_corporate_actions_corporate_action_not_self_superseding",
            "supersedes_action_id IS NULL OR supersedes_action_id <> id",
        )
        batch_op.create_unique_constraint(
            "uq_corporate_actions_supersedes_action_id",
            ["supersedes_action_id"],
        )
        batch_op.create_foreign_key(
            "fk_corporate_actions_supersedes_action_id_corporate_actions",
            "corporate_actions",
            ["supersedes_action_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ix_corporate_actions_security_available_at",
            ["security_id", "available_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("corporate_actions") as batch_op:
        batch_op.drop_index("ix_corporate_actions_security_available_at")
        batch_op.drop_constraint(
            "fk_corporate_actions_supersedes_action_id_corporate_actions",
            type_="foreignkey",
        )
        batch_op.drop_constraint("uq_corporate_actions_supersedes_action_id", type_="unique")
        batch_op.drop_constraint(
            "ck_corporate_actions_corporate_action_not_self_superseding",
            type_="check",
        )
        batch_op.drop_column("supersedes_action_id")
        batch_op.drop_column("available_at")
        batch_op.drop_column("source_published_at")
