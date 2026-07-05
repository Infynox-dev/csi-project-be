"""add number fields to unit_member_change_request

Revision ID: m028
Revises: m027
"""

revision = 'm028'
down_revision = 'm027'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column(
        'unit_member_change_request',
        sa.Column('number', sa.String(length=30), nullable=True),
    )
    op.add_column(
        'unit_member_change_request',
        sa.Column('original_number', sa.String(length=30), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('unit_member_change_request', 'original_number')
    op.drop_column('unit_member_change_request', 'number')
