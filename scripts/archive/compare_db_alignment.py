"""Compare dev/prod schema alignment for location master data."""

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
        tables = [
            row[0]
            for row in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name IN ('country','state','city') "
                    "ORDER BY table_name"
                )
            ).fetchall()
        ]
        cols = [
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='unit_members' AND column_name IN "
                    "('residence_city_id','residence_state_id') "
                    "ORDER BY column_name"
                )
            ).fetchall()
        ]
        city_cols = [
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='city' AND column_name='state_id'"
                )
            ).fetchall()
        ]
        counts = {}
        if "country" in tables:
            counts["countries"] = conn.execute(text("SELECT COUNT(*) FROM country")).scalar()
        if "state" in tables:
            counts["states"] = conn.execute(text("SELECT COUNT(*) FROM state")).scalar()
        if "city" in tables:
            counts["cities"] = conn.execute(text("SELECT COUNT(*) FROM city")).scalar()
            counts["cities_with_state"] = conn.execute(
                text("SELECT COUNT(*) FROM city WHERE state_id IS NOT NULL")
            ).scalar()

        members_with_residence = conn.execute(
            text(
                "SELECT COUNT(*) FROM unit_members "
                "WHERE residence_location IS NOT NULL "
                "OR residence_state_id IS NOT NULL OR residence_city_id IS NOT NULL"
            )
        ).scalar()

        result = {
            "label": label,
            "alembic_version": rev,
            "master_tables": tables,
            "unit_members_cols": cols,
            "city_has_state_id": bool(city_cols),
            "counts": counts,
            "members_with_residence": members_with_residence,
        }
        print(f"=== {label} ===")
        print("alembic_version:", rev)
        print("master_tables:", tables)
        print("unit_members_cols:", cols)
        print("city.state_id:", bool(city_cols))
        print("counts:", counts if counts else "n/a")
        print("members_with_residence:", members_with_residence)
        print()
        return result


if __name__ == "__main__":
    dev = inspect("DEV", DEV)
    prod = inspect("PROD", PROD)

    aligned = (
        dev["alembic_version"] == prod["alembic_version"]
        and dev["master_tables"] == prod["master_tables"]
        and dev["unit_members_cols"] == prod["unit_members_cols"]
        and dev["city_has_state_id"] == prod["city_has_state_id"]
        and dev.get("counts") == prod.get("counts")
    )
    print("ALIGNED:", aligned)
