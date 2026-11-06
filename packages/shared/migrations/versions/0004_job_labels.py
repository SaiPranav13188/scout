"""job labels (my apply/maybe/no judgements for the matching eval set)

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    label = postgresql.ENUM("apply", "maybe", "no", name="job_label")
    label.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "job_labels",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("label", postgresql.ENUM(name="job_label", create_type=False), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
    )
    op.execute("ALTER TABLE job_labels ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("job_labels")
    op.execute("DROP TYPE IF EXISTS job_label")
