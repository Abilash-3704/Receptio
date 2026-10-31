"""Remember when each Call was admitted, so settlement can check a claimed length against it.

Admissions made before this migration have no time and settle on the claimed length alone.

Revision ID: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"


def upgrade() -> None:
    op.add_column("call_admissions", sa.Column("admitted_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("call_admissions", "admitted_at")
