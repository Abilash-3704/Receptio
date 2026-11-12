"""Baseline: the seven tables ticket 03 created with create_all.

Revision ID: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "providers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("role", sa.String(32), nullable=False),
    )
    op.create_table(
        "services",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("duration_minutes", sa.Integer, nullable=False),
    )
    op.create_table(
        "provider_services",
        sa.Column("provider_id", sa.Integer, sa.ForeignKey("providers.id"), primary_key=True),
        sa.Column("service_id", sa.Integer, sa.ForeignKey("services.id"), primary_key=True),
    )
    op.create_table(
        "provider_working_hours",
        sa.Column("provider_id", sa.Integer, sa.ForeignKey("providers.id"), primary_key=True),
        sa.Column("weekday", sa.Integer, primary_key=True),
        sa.Column("opens", sa.Time, nullable=False),
        sa.Column("closes", sa.Time, nullable=False),
    )
    op.create_table(
        "clinic_hours",
        sa.Column("weekday", sa.Integer, primary_key=True),
        sa.Column("opens", sa.Time, nullable=False),
        sa.Column("closes", sa.Time, nullable=False),
    )
    op.create_table(
        "appointments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("provider_id", sa.Integer, sa.ForeignKey("providers.id"), nullable=False),
        sa.Column("service_id", sa.Integer, sa.ForeignKey("services.id"), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("patient_name", sa.String(80)),
    )
    op.create_table(
        "calls",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("transcript", sa.JSON, nullable=False),
    )


def downgrade() -> None:
    for table in (
        "calls",
        "appointments",
        "clinic_hours",
        "provider_working_hours",
        "provider_services",
        "services",
        "providers",
    ):
        op.drop_table(table)
