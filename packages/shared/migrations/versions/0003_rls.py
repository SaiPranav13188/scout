"""row level security

Every table gets RLS with no policies. The API and workers connect as the table owner, which
bypasses RLS. Any other role (e.g. Supabase's auto-generated REST API roles `anon` and
`authenticated`) can read nothing.

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TABLES = [
    "profile", "settings", "saved_searches", "companies", "company_watchlist", "jobs",
    "master_resume", "job_scores", "resumes_generated", "applications", "recruiters", "outreach",
    "notifications", "agent_runs", "audit_log", "secrets_encrypted", "alembic_version",
]


def upgrade() -> None:
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
