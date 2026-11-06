"""extensions

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # gen_random_uuid() is built in since Postgres 13. pgvector: embeddings column comes in Phase 3.
    # Extensions live in their own schema (Supabase's recommendation; keeps `public` clean).
    # Supabase's default search_path already includes `extensions`.
    op.execute("CREATE SCHEMA IF NOT EXISTS extensions")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions")


def downgrade() -> None:
    pass  # extensions may be shared with other schemas; never drop them automatically
