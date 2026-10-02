"""platform baseline: extensions and one schema per module

Revision ID: 952fc5a6ac7d
Revises:
Create Date: 2026-10-02 17:30:46.071422+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "952fc5a6ac7d"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Written out here rather than imported: a migration must not change when the code does.
EXTENSIONS = ("postgis", "vector", "pg_trgm", "unaccent", "pgcrypto")
SCHEMAS = (
    "platform",
    "identity",
    "catalog",
    "subscriptions",
    "orders",
    "billing",
    "hub_ops",
    "logistics",
    "partners",
    "community",
    "intel",
    "notifications",
    "analytics",
    "assistant",
)


def upgrade() -> None:
    for extension in EXTENSIONS:
        op.execute(f"CREATE EXTENSION IF NOT EXISTS {extension}")
    for schema in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")


def downgrade() -> None:
    # "platform" stays: it holds Alembic's own version table. Extensions stay too, since other
    # tools (and other databases' objects) may rely on them.
    for schema in reversed(SCHEMAS):
        if schema != "platform":
            op.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
