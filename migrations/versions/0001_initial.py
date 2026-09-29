"""courts and clips tables

Revision ID: 0001
Revises:
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

clip_status = sa.Enum("processing", "ready", "failed", name="clip_status")


def upgrade() -> None:
    op.create_table(
        "courts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "clips",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("court_id", sa.String(64), sa.ForeignKey("courts.id"), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_s", sa.Float, nullable=True),
        sa.Column("file_key", sa.Text, nullable=True),
        sa.Column("thumb_key", sa.Text, nullable=True),
        sa.Column("status", clip_status, nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_clips_court_id_triggered_at", "clips", ["court_id", "triggered_at"])


def downgrade() -> None:
    op.drop_index("ix_clips_court_id_triggered_at", table_name="clips")
    op.drop_table("clips")
    op.drop_table("courts")
    clip_status.drop(op.get_bind(), checkfirst=True)
