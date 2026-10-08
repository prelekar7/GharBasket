"""identity: a `whatsapp` consent purpose for updates on WhatsApp (M1 §3.2, M11 §3.1)

Revision ID: 7c3e91b2d5a4
Revises: a8e9dd07f72c
Create Date: 2026-10-08 19:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "7c3e91b2d5a4"
down_revision: str | Sequence[str] | None = "a8e9dd07f72c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BEFORE = (
    "purpose IN ('service_delivery', 'personalisation', 'health_personalisation', "
    "'marketing', 'research_anonymised')"
)
AFTER = (
    "purpose IN ('service_delivery', 'personalisation', 'health_personalisation', "
    "'marketing', 'research_anonymised', 'whatsapp')"
)


def upgrade() -> None:
    op.drop_constraint(op.f("ck_consents_purpose"), "consents", schema="identity", type_="check")
    op.create_check_constraint(op.f("ck_consents_purpose"), "consents", AFTER, schema="identity")


def downgrade() -> None:
    op.execute("DELETE FROM identity.consents WHERE purpose = 'whatsapp'")
    op.drop_constraint(op.f("ck_consents_purpose"), "consents", schema="identity", type_="check")
    op.create_check_constraint(op.f("ck_consents_purpose"), "consents", BEFORE, schema="identity")
