"""notifications: app copy (en/hi/mr) for the cut-over's message to the admins
(`billing.cutover_completed`, M5 §3.10, M11 §3.1 event map)

Revision ID: 27eeef60dbc7
Revises: 6c3f1a9d2e58
Create Date: 2026-11-15 16:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "27eeef60dbc7"
down_revision: str | Sequence[str] | None = "6c3f1a9d2e58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KIND = "billing.cutover_completed"
# (locale, (title, body)); {total} is the closing entry's amount and {accounts} how many accounts
# it zeroed. Reviewed by Rhythm Thakur and Manish Raje before release (M11 §9); edits go through
# PUT /v1/admin/notification-templates.
COPY: list[tuple[str, tuple[str, str]]] = [
    (
        "en-IN",
        (
            "Test money closed",
            "The cut-over closed {total} of test money in {accounts} accounts. Every account "
            "starts at ₹0, and payments are live now.",
        ),
    ),
    (
        "hi-IN",
        (
            "टेस्ट पैसा बंद हुआ",
            "कट-ओवर ने {accounts} खातों में {total} का टेस्ट पैसा बंद किया। अब हर खाता ₹0 से "
            "शुरू है, और भुगतान असली पैसे से होते हैं।",
        ),
    ),
    (
        "mr-IN",
        (
            "टेस्ट पैसे बंद झाले",
            "कट-ओव्हरने {accounts} खात्यांमधील {total} चे टेस्ट पैसे बंद केले. आता प्रत्येक खाते "
            "₹0 पासून सुरू होते, आणि पेमेंट खऱ्या पैशांनी होतात.",
        ),
    ),
]

templates = sa.table(
    "templates",
    sa.column("kind", sa.String),
    sa.column("channel", sa.String),
    sa.column("locale", sa.String),
    sa.column("version", sa.SmallInteger),
    sa.column("title", sa.String),
    sa.column("body", sa.String),
    sa.column("active", sa.Boolean),
    schema="notifications",
)


def upgrade() -> None:
    op.bulk_insert(
        templates,
        [
            {
                "kind": KIND,
                "channel": "app",
                "locale": locale,
                "version": 1,
                "title": title,
                "body": body,
                "active": True,
            }
            for locale, (title, body) in COPY
        ],
    )


def downgrade() -> None:
    # Every version of the kind, including ones saved later through the console.
    op.execute(templates.delete().where(templates.c.kind == KIND))
