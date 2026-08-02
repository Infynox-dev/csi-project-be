#!/usr/bin/env python3
"""Normalize legacy member gender values to canonical M/F codes."""

from __future__ import annotations

import os
import sys

import psycopg
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.units.gender_utils import normalize_member_gender  # noqa: E402

TABLES = (
    ("unit_members", "gender"),
    ("archived_unit_member", "gender"),
    ("removed_unit_member", "gender"),
    ("unit_member_add_request", "gender"),
    ("unit_member_change_request", "gender"),
    ("unit_member_change_request", "original_gender"),
)


def main() -> None:
    load_dotenv(os.path.join(ROOT, ".env"))
    database_url = os.getenv("DATABASE_URL", "").replace("postgresql+psycopg://", "postgresql://")
    if not database_url:
        raise SystemExit("DATABASE_URL is not configured")

    updated_total = 0
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            for table, column in TABLES:
                cur.execute(
                    f"""
                    SELECT id, {column}
                    FROM {table}
                    WHERE {column} IS NOT NULL
                      AND {column} NOT IN ('M', 'F')
                    """
                )
                rows = cur.fetchall()
                for row_id, gender in rows:
                    normalized = normalize_member_gender(gender)
                    if normalized in ("M", "F") and normalized != gender:
                        cur.execute(
                            f"UPDATE {table} SET {column} = %s WHERE id = %s",
                            (normalized, row_id),
                        )
                        updated_total += 1
                        print(f"updated {table}.{column} id={row_id}: {gender!r} -> {normalized!r}")
            conn.commit()

    print(f"Done. Updated {updated_total} row(s).")


if __name__ == "__main__":
    main()
