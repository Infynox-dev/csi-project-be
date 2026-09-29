"""conference payment district ledger columns

Revision ID: m030
Revises: m029
"""

revision = "m030"
down_revision = "m029"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    op.add_column(
        "conference_payment",
        sa.Column("clergy_district_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_conference_payment_clergy_district_id",
        "conference_payment",
        ["clergy_district_id"],
    )
    op.create_index(
        "ix_conference_payment_conference_district",
        "conference_payment",
        ["conference_id", "clergy_district_id"],
    )
    op.create_foreign_key(
        "fk_conference_payment_clergy_district",
        "conference_payment",
        "clergy_district",
        ["clergy_district_id"],
        ["id"],
    )
    op.add_column("conference_payment", sa.Column("total_amount", sa.Integer(), nullable=True))
    op.add_column("conference_payment", sa.Column("balance_amount", sa.Integer(), nullable=True))
    op.add_column(
        "conference_payment",
        sa.Column("approved_paid_amount", sa.Integer(), nullable=True),
    )
    op.add_column("conference_payment", sa.Column("rejection_note", sa.Text(), nullable=True))
    op.add_column("conference_payment", sa.Column("reviewed_at", sa.DateTime(), nullable=True))
    op.add_column(
        "conference_payment",
        sa.Column("reviewed_by_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_conference_payment_reviewed_by",
        "conference_payment",
        "custom_user",
        ["reviewed_by_id"],
        ["id"],
    )
    op.execute(
        """
        UPDATE conference_payment p
        SET clergy_district_id = u.clergy_district_id,
            total_amount = COALESCE(p.total_amount, CAST(p.amount_to_pay AS INTEGER))
        FROM custom_user u
        WHERE p.uploaded_by_id = u.id
          AND p.clergy_district_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_conference_payment_reviewed_by", "conference_payment", type_="foreignkey")
    op.drop_constraint(
        "fk_conference_payment_clergy_district", "conference_payment", type_="foreignkey"
    )
    op.drop_index("ix_conference_payment_conference_district", table_name="conference_payment")
    op.drop_index("ix_conference_payment_clergy_district_id", table_name="conference_payment")
    op.drop_column("conference_payment", "reviewed_by_id")
    op.drop_column("conference_payment", "reviewed_at")
    op.drop_column("conference_payment", "rejection_note")
    op.drop_column("conference_payment", "approved_paid_amount")
    op.drop_column("conference_payment", "balance_amount")
    op.drop_column("conference_payment", "total_amount")
    op.drop_column("conference_payment", "clergy_district_id")
