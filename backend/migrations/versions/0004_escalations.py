"""Escalations: Handoffs and Callback Requests, each with a reason.

Revision ID: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.create_table(
        "escalations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("call_id", sa.Integer, sa.ForeignKey("calls.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("caller_phone", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("escalations")
