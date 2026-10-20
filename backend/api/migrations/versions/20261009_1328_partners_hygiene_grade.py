"""partners: a kitchen's hygiene grade on its application, given at approval (M8 §3.1)

Revision ID: 98790006ab63
Revises: afbb215623de
Create Date: 2026-10-09 13:28:17.298174+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "98790006ab63"
down_revision: str | Sequence[str] | None = "afbb215623de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("hygiene_grade", sa.String(length=1), nullable=True),
        schema="partners",
    )
    # Autogenerate does not see check constraints added to an existing table.
    op.create_check_constraint(
        op.f("ck_applications_hygiene_grade"),
        "applications",
        "hygiene_grade IS NULL OR hygiene_grade IN ('A', 'B', 'C')",
        schema="partners",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_applications_hygiene_grade"), "applications", type_="check", schema="partners"
    )
    op.drop_column("applications", "hygiene_grade", schema="partners")
