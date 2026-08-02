"""Clear all member residence/location data for re-entry via country/city master tables."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.auth.models  # noqa: F401
import app.units.models  # noqa: F401

from sqlalchemy import func, select, update

from app.auth.models import UnitMembers
from app.common.db import session_scope


def clear_member_residence() -> None:
    with session_scope() as db:
        before = db.scalar(
            select(func.count())
            .select_from(UnitMembers)
            .where(
                (UnitMembers.residence_location.is_not(None))
                | (UnitMembers.residence_state_id.is_not(None))
                | (UnitMembers.residence_city_id.is_not(None))
            )
        ) or 0

        db.execute(
            update(UnitMembers).values(
                residence_location=None,
                residence_state_id=None,
                residence_city_id=None,
            )
        )

        after = db.scalar(
            select(func.count())
            .select_from(UnitMembers)
            .where(
                (UnitMembers.residence_location.is_not(None))
                | (UnitMembers.residence_state_id.is_not(None))
                | (UnitMembers.residence_city_id.is_not(None))
            )
        ) or 0

        print(f"Cleared residence data for {before} members.")
        print(f"Members still with location data: {after}")


if __name__ == "__main__":
    clear_member_residence()
