"""notifications: app copy (en/hi/mr) for the two staff messages of the ops alerts (ADR-0033, M11
§3.1 event map): a reconciliation run opened items (`billing.reconciliation_mismatch`, to the
admins) and a route plan was left for review (`logistics.plan_needs_review`, to the hub's managers
and the admins)

Revision ID: 8b2e5d4c7a19
Revises: 3f8c1a6d9e20
Create Date: 2026-11-10 18:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8b2e5d4c7a19"
down_revision: str | Sequence[str] | None = "3f8c1a6d9e20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = ("billing.reconciliation_mismatch", "logistics.plan_needs_review")
# (kind, locale, (title, body)); {opened} is a count and {kinds} the run's kinds in the reader's
# words; {hub} is the hub's name and {day} the delivery date's weekday. Reviewed by Rhythm Thakur
# and Manish Raje before release (M11 §9); edits go through PUT /v1/admin/notification-templates.
COPY: list[tuple[str, str, tuple[str, str]]] = [
    (
        KINDS[0],
        "en-IN",
        (
            "Payment check: {opened} to look at",
            "New in this run: {kinds}. Open Reconciliation to look.",
        ),
    ),
    (
        KINDS[0],
        "hi-IN",
        (
            "भुगतान जाँच: {opened} मामले देखने हैं",
            "इस बार नए: {kinds}। देखने के लिए रिकंसिलिएशन खोलें।",
        ),
    ),
    (
        KINDS[0],
        "mr-IN",
        (
            "पेमेंट तपासणी: {opened} प्रकरणे पाहायची आहेत",
            "या वेळी नवीन: {kinds}. पाहण्यासाठी रिकन्सिलिएशन उघडा.",
        ),
    ),
    (
        KINDS[1],
        "en-IN",
        (
            "Routes for {hub} need a look",
            "{day}'s plan has problems and has not gone out. Open Routes to check and publish it.",
        ),
    ),
    (
        KINDS[1],
        "hi-IN",
        (
            "{hub} के रूट देखने हैं",
            "{day} के प्लान में दिक्कतें हैं, वह अभी भेजा नहीं गया। जाँचकर पब्लिश करने के लिए रूट्स खोलें।",
        ),
    ),
    (
        KINDS[1],
        "mr-IN",
        (
            "{hub} चे रूट पाहायचे आहेत",
            "{day}च्या प्लॅनमध्ये अडचणी आहेत, तो अजून पाठवलेला नाही. तपासून पब्लिश करण्यासाठी रूट्स उघडा.",
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
                "kind": kind,
                "channel": "app",
                "locale": locale,
                "version": 1,
                "title": title,
                "body": body,
                "active": True,
            }
            for kind, locale, (title, body) in COPY
        ],
    )


def downgrade() -> None:
    # Every version of the kinds, including ones saved later through the console.
    op.execute(templates.delete().where(templates.c.kind.in_(KINDS)))
