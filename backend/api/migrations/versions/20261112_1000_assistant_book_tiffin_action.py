"""assistant: the `book_tiffin` proposal action (M13 §3.1)

Revision ID: 3c7a9e1f5b28
Revises: 5d1e9b3a7c42
Create Date: 2026-11-12 10:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "3c7a9e1f5b28"
down_revision: str | Sequence[str] | None = "5d1e9b3a7c42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIONS = ("edit_box_line", "skip_week")
NEW_ACTIONS = ("book_tiffin",)
TABLE = "assistant.proposals"


def _actions(actions: tuple[str, ...]) -> None:
    listed = ", ".join(repr(a) for a in actions)
    op.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT ck_proposals_action")
    op.execute(
        f"ALTER TABLE {TABLE} ADD CONSTRAINT ck_proposals_action CHECK (action IN ({listed}))"
    )


def upgrade() -> None:
    _actions(ACTIONS + NEW_ACTIONS)


def downgrade() -> None:
    # The cards are 15-minute proposals; the booking itself lives in M4 and the outbox keeps the
    # audit event, so the cards of the new action are dropped to restore the narrower check.
    op.execute("DELETE FROM assistant.proposals WHERE action = 'book_tiffin'")
    _actions(ACTIONS)
