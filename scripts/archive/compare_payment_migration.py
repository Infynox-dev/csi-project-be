"""Compare dev/prod alignment for payment migrations."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text

DEV = (
    "postgresql+psycopg://neondb_owner:npg_mcp40TxrFHVC@"
    "ep-snowy-heart-a1z65pya-pooler.ap-southeast-1.aws.neon.tech/csi_youth_db"
    "?sslmode=require&channel_binding=require"
)
PROD = (
    "postgresql+psycopg://neondb_owner:npg_mcp40TxrFHVC@"
    "ep-round-waterfall-a13doc66-pooler.ap-southeast-1.aws.neon.tech/csi_youth_db"
    "?sslmode=require&channel_binding=require"
)


def inspect(label: str, url: str) -> dict:
    engine = create_engine(url)
    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        payment_cols = [
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='unit_registration_payment' "
                    "ORDER BY ordinal_position"
                )
            ).fetchall()
        ]
        has_balance = "balance_amount" in payment_cols
        payment_count = conn.execute(
            text("SELECT COUNT(*) FROM unit_registration_payment")
        ).scalar()

        result = {
            "label": label,
            "alembic_version": rev,
            "payment_columns": payment_cols,
            "has_balance_amount": has_balance,
            "payment_rows": payment_count,
        }
        print(f"=== {label} ===")
        print("alembic_version:", rev)
        print("has balance_amount:", has_balance)
        print("payment columns:", payment_cols)
        print("payment rows:", payment_count)
        print()
        return result


if __name__ == "__main__":
    dev = inspect("DEV", DEV)
    prod = inspect("PROD", PROD)

    aligned = (
        dev["alembic_version"] == prod["alembic_version"]
        and dev["has_balance_amount"] == prod["has_balance_amount"]
        and dev["payment_columns"] == prod["payment_columns"]
    )
    print("ALIGNED:", aligned)
