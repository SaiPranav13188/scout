"""job_scores.profile_hash (which resume a score used); one active master resume, unique versions

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_scores", sa.Column("profile_hash", sa.Text(), nullable=True))
    op.create_unique_constraint("uq_master_resume_version", "master_resume", ["version"])
    op.create_index("uq_master_resume_one_active", "master_resume", ["is_active"], unique=True,
                    postgresql_where=sa.text("is_active"))


def downgrade() -> None:
    op.drop_index("uq_master_resume_one_active", table_name="master_resume")
    op.drop_constraint("uq_master_resume_version", "master_resume")
    op.drop_column("job_scores", "profile_hash")
