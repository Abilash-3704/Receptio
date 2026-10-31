"""Cost guard: daily capacity counters and admitted Calls.

Revision ID: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade() -> None:
    op.create_table(
        "capacity_counters",
        sa.Column("scope", sa.String(16), primary_key=True),
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("used", sa.Integer, nullable=False, server_default="0"),
        sa.CheckConstraint("used >= 0", name="capacity_counters_used_non_negative"),
    )
    op.create_table(
        "call_admissions",
        sa.Column("room", sa.String(64), primary_key=True),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("reserved_seconds", sa.Integer, nullable=False),
        sa.Column("settled", sa.Boolean, nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_table("call_admissions")
    op.drop_table("capacity_counters")
