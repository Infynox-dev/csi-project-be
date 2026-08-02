"""
One-time script to copy all Yuvalokham data from development to production.
Copies data in FK-safe order and resets sequences to avoid ID conflicts.
"""

import psycopg

DEV_DSN = "postgresql://neondb_owner:npg_mcp40TxrFHVC@ep-snowy-heart-a1z65pya-pooler.ap-southeast-1.aws.neon.tech/csi_youth_db?sslmode=require"
PROD_DSN = "postgresql://neondb_owner:npg_mcp40TxrFHVC@ep-round-waterfall-a13doc66-pooler.ap-southeast-1.aws.neon.tech/csi_youth_db?sslmode=require"

TABLES_IN_ORDER = [
    "ym_user",
    "ym_subscription_plan",
    "ym_magazine",
    "ym_qr_setting",
    "ym_refresh_token",
    "ym_complaint",
    "ym_subscription",
    "ym_payment",
]

def get_columns(cur, table):
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = %s ORDER BY ordinal_position",
        (table,),
    )
    return [r[0] for r in cur.fetchall()]

def main():
    dev = psycopg.connect(DEV_DSN)
    prod = psycopg.connect(PROD_DSN)

    try:
        dev_cur = dev.cursor()
        prod_cur = prod.cursor()

        for table in TABLES_IN_ORDER:
            cols = get_columns(dev_cur, table)
            col_list = ", ".join(cols)
            placeholders = ", ".join(["%s"] * len(cols))

            dev_cur.execute(f"SELECT {col_list} FROM {table} ORDER BY id")
            rows = dev_cur.fetchall()

            if not rows:
                print(f"  {table}: 0 rows (skip)")
                continue

            prod_cur.execute(f"DELETE FROM {table}")

            for row in rows:
                prod_cur.execute(
                    f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
                    row,
                )
            print(f"  {table}: {len(rows)} rows copied")

            if "id" in cols and table != "ym_qr_setting":
                seq_name = f"{table}_id_seq"
                prod_cur.execute(
                    f"SELECT setval('{seq_name}', (SELECT COALESCE(MAX(id), 1) FROM {table}))"
                )

        prod.commit()
        print("\nDone — all Yuvalokham data copied to production.")

    except Exception as e:
        prod.rollback()
        print(f"\nERROR: {e}")
        raise
    finally:
        dev.close()
        prod.close()

if __name__ == "__main__":
    main()
