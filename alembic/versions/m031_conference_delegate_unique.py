"""allow multiple member delegates per official

Revision ID: m031
Revises: m030
"""

revision = "m031"
down_revision = "m030"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    op.drop_constraint("uq_delegate_per_conf_official", "conference_delegate", type_="unique")
    op.create_index(
        "uq_delegate_member_per_conference",
        "conference_delegate",
        ["conference_id", "members_id"],
        unique=True,
        postgresql_where="members_id IS NOT NULL",
    )
    op.create_index(
        "uq_delegate_official_placeholder",
        "conference_delegate",
        ["conference_id", "officials_id"],
        unique=True,
        postgresql_where="members_id IS NULL",
    )


def downgrade() -> None:
    op.drop_index("uq_delegate_official_placeholder", table_name="conference_delegate")
    op.drop_index("uq_delegate_member_per_conference", table_name="conference_delegate")
    op.create_unique_constraint(
        "uq_delegate_per_conf_official",
        "conference_delegate",
        ["conference_id", "officials_id"],
    )
