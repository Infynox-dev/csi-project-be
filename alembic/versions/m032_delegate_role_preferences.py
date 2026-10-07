"""per-person role and preferences on conference delegates

Revision ID: m032
Revises: m031
"""

revision = "m032"
down_revision = "m031"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    op.add_column("conference_delegate", sa.Column("attendee_role", sa.String(length=16), nullable=True))
    op.add_column("conference_delegate", sa.Column("food_preference", sa.String(length=16), nullable=True))
    op.add_column(
        "conference_delegate",
        sa.Column("accommodation_required", sa.Boolean(), nullable=True),
    )
    op.execute(
        "UPDATE conference_delegate SET attendee_role = 'official' WHERE members_id IS NULL"
    )
    op.execute(
        "UPDATE conference_delegate SET attendee_role = 'delegate' WHERE members_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("conference_delegate", "accommodation_required")
    op.drop_column("conference_delegate", "food_preference")
    op.drop_column("conference_delegate", "attendee_role")
