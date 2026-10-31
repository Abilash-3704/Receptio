"""Confirmation Messages: the mocked message recorded for each Confirmed Appointment.

Revision ID: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.create_table(
        "confirmation_messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "appointment_id",
            sa.Integer,
            sa.ForeignKey("appointments.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("to_phone", sa.String(20), nullable=False),
        sa.Column("body", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("confirmation_messages")
