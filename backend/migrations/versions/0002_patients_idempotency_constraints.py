"""Patients, idempotency keys, read-back and reschedule links, and the database backstop for
double-booking.

The partial exclusion constraints are the last line of defence for ADR-0001: even if a code
path forgot a row lock, Postgres refuses a second Confirmed Appointment that overlaps in time
for the same Provider, or for the same Patient regardless of Provider.

Revision ID: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"

# Patient created for visits that existed before Patients did, matched by name only.
PLACEHOLDER_PHONE = "+910000000000"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "patients",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("full_name", sa.String(80), nullable=False),
        sa.Column("name_key", sa.String(80), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.UniqueConstraint("name_key", "phone"),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("scope", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status_code", sa.Integer),
        sa.Column("response", sa.JSON),
    )
    op.add_column("appointments", sa.Column("patient_id", sa.Integer, sa.ForeignKey("patients.id")))
    op.add_column("appointments", sa.Column("read_back_at", sa.DateTime(timezone=True)))
    op.add_column(
        "appointments", sa.Column("replaces_id", sa.Integer, sa.ForeignKey("appointments.id"))
    )
    op.execute(
        "INSERT INTO patients (full_name, name_key, phone) "
        f"SELECT DISTINCT patient_name, lower(patient_name), '{PLACEHOLDER_PHONE}' "
        "FROM appointments WHERE patient_name IS NOT NULL"
    )
    op.execute(
        "UPDATE appointments SET patient_id = p.id FROM patients p "
        "WHERE appointments.patient_name = p.full_name"
    )
    op.drop_column("appointments", "patient_name")
    op.execute(
        "ALTER TABLE appointments ADD CONSTRAINT appointments_no_provider_overlap "
        "EXCLUDE USING gist (provider_id WITH =, tstzrange(starts_at, ends_at) WITH &&) "
        "WHERE (status = 'confirmed')"
    )
    op.execute(
        "ALTER TABLE appointments ADD CONSTRAINT appointments_no_patient_overlap "
        "EXCLUDE USING gist (patient_id WITH =, tstzrange(starts_at, ends_at) WITH &&) "
        "WHERE (status = 'confirmed')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE appointments DROP CONSTRAINT appointments_no_patient_overlap")
    op.execute("ALTER TABLE appointments DROP CONSTRAINT appointments_no_provider_overlap")
    op.add_column("appointments", sa.Column("patient_name", sa.String(80)))
    op.execute(
        "UPDATE appointments SET patient_name = p.full_name FROM patients p "
        "WHERE appointments.patient_id = p.id"
    )
    op.drop_column("appointments", "replaces_id")
    op.drop_column("appointments", "read_back_at")
    op.drop_column("appointments", "patient_id")
    op.drop_table("idempotency_keys")
    op.drop_table("patients")
