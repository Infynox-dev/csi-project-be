"""add conference_settings singleton for delegate fee

Revision ID: m029
Revises: m028
"""

revision = 'm029'
down_revision = 'm028'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.create_table(
        'conference_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('delegate_fee', sa.Integer(), nullable=False, server_default='300'),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.execute(
        "INSERT INTO conference_settings (id, delegate_fee, updated_at) VALUES (1, 300, now())"
    )


def downgrade() -> None:
    op.drop_table('conference_settings')
