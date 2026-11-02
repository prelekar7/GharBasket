"""notifications: app copy (en/hi/mr) for the message the admins get when a model drifts
(`intel.drift_detected`, M11 §3.1 event map, M10 §6)

Revision ID: 3f8c1a6d9e20
Revises: 5d3c8a17e2b4
Create Date: 2026-11-02 10:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3f8c1a6d9e20"
down_revision: str | Sequence[str] | None = "5d3c8a17e2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KIND = "intel.drift_detected"
# (locale, (title, body)); {live} and {backtest} are WAPE shown as a percentage. Reviewed by
# Rhythm Thakur and Manish Raje before release (M11 §9); edits go through
# PUT /v1/admin/notification-templates.
COPY: list[tuple[str, tuple[str, str]]] = [
    (
        "en-IN",
        (
            "Forecast drift at {hub}",
            "Live WAPE {live} vs {backtest} in the backtest. Open AI health to look.",
        ),
    ),
    (
        "hi-IN",
        (
            "{hub} पर फ़ोरकास्ट में गड़बड़ी",
            "लाइव WAPE {live}, बैकटेस्ट में {backtest}। देखने के लिए AI हेल्थ खोलें।",
        ),
    ),
    (
        "mr-IN",
        (
            "{hub} येथे फोरकास्ट बिघडला",
            "लाइव्ह WAPE {live}, बॅकटेस्टमध्ये {backtest}. पाहण्यासाठी AI हेल्थ उघडा.",
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
