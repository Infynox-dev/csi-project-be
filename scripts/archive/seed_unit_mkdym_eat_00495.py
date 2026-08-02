"""Seed MKDYM/EAT/00495 (VAIKKOM) with 100 members and councilors."""

import os
import random
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, func, select

from app.auth.models import (
    CustomUser,
    ResidenceLocation,
    UnitCouncilor,
    UnitDetails,
    UnitMembers,
)
from app.common.db import session_scope

TARGET_USERNAME = "MKDYM/EAT/00495"
TARGET_MEMBER_COUNT = 100

BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
QUALIFICATIONS = [
    "Student",
    "B.Com",
    "B.Sc",
    "B.A",
    "B.Tech",
    "M.Com",
    "M.Sc",
    "Diploma",
    "B.Ed",
    "Nursing",
    "Engineer",
    "Teacher",
]
RESIDENCE_OPTIONS = list(ResidenceLocation)

FIRST_NAMES_M = [
    "ABHISHEK", "AKHIL", "ANIL", "BINU", "DEEPU", "EMIL", "GEORGE", "JOHN",
    "JACOB", "JAMES", "JOEL", "JOHNSON", "JOSHUA", "KEVIN", "LENIN", "MANU",
    "NIKHIL", "NINAN", "RAHUL", "ROBIN", "SAMUEL", "SHINE", "SUSHANTH", "VINAYAN",
]
FIRST_NAMES_F = [
    "ANI", "ANCY", "ANU", "DIVYA", "IRENE", "JESSY", "MARIAM", "MOLLY",
    "REBA", "SHERON", "SHIJIMOL", "SUMIMOL", "SWETHA", "TESSY", "ANCY", "SNEHA",
]
LAST_NAMES = [
    "MATHEWS", "THOMAS", "GEORGE", "JACOB", "KOSHY", "VARUGHESE", "JOY", "ROY",
    "SAMUEL", "ABRAHAM", "NINAN", "LENIN", "JOSE", "PAUL", "PHILIP", "CHACKO",
]


def councilor_count_for_members(member_count: int) -> int:
    if member_count <= 25:
        return 1
    if member_count <= 50:
        return 2
    if member_count <= 75:
        return 3
    if member_count <= 100:
        return 4
    return 5


def generate_member_name(index: int) -> tuple[str, str]:
    gender = "M" if index % 2 == 0 else "F"
    first_pool = FIRST_NAMES_M if gender == "M" else FIRST_NAMES_F
    first = first_pool[(index // 2) % len(first_pool)]
    last = LAST_NAMES[index % len(LAST_NAMES)]
    return f"{first} {last}", gender


def generate_dob(index: int) -> date:
    year = 1990 + (index % 16)
    month = (index % 12) + 1
    day = (index % 27) + 1
    return date(year, month, day)


def generate_phone(user_id: int, index: int) -> str:
    suffix = f"{user_id:03d}{index:04d}"[-10:]
    return f"9{suffix}"


def seed() -> None:
    with session_scope() as db:
        user = db.execute(
            select(CustomUser).where(CustomUser.username == TARGET_USERNAME)
        ).scalar_one_or_none()

        if not user:
            print(f"Unit user not found: {TARGET_USERNAME}")
            sys.exit(1)

        user_id = user.id
        existing_members = list(
            db.execute(
                select(UnitMembers)
                .where(UnitMembers.registered_user_id == user_id)
                .order_by(UnitMembers.id)
            ).scalars()
        )
        existing_count = len(existing_members)

        print(f"Found unit {TARGET_USERNAME} (user_id={user_id})")
        print(f"Existing members: {existing_count}")

        if existing_count >= TARGET_MEMBER_COUNT:
            print(f"Already has {existing_count} members (target: {TARGET_MEMBER_COUNT})")
        else:
            to_add = TARGET_MEMBER_COUNT - existing_count
            print(f"Adding {to_add} members...")

            for i in range(existing_count, TARGET_MEMBER_COUNT):
                name, gender = generate_member_name(i)
                member = UnitMembers(
                    registered_user_id=user_id,
                    name=name,
                    gender=gender,
                    dob=generate_dob(i),
                    number=generate_phone(user_id, i),
                    qualification=QUALIFICATIONS[i % len(QUALIFICATIONS)],
                    blood_group=BLOOD_GROUPS[i % len(BLOOD_GROUPS)],
                    residence_location=RESIDENCE_OPTIONS[i % len(RESIDENCE_OPTIONS)],
                )
                db.add(member)

            db.flush()

        all_members = list(
            db.execute(
                select(UnitMembers)
                .where(UnitMembers.registered_user_id == user_id)
                .order_by(UnitMembers.name)
            ).scalars()
        )
        member_count = len(all_members)
        required_councilors = councilor_count_for_members(member_count)

        db.execute(
            delete(UnitCouncilor).where(UnitCouncilor.registered_user_id == user_id)
        )
        db.flush()

        councilor_candidates = all_members[:required_councilors]
        for member in councilor_candidates:
            db.add(
                UnitCouncilor(
                    registered_user_id=user_id,
                    unit_member_id=member.id,
                )
            )

        db.flush()

        unit_details = db.execute(
            select(UnitDetails).where(UnitDetails.registered_user_id == user_id)
        ).scalar_one_or_none()
        if unit_details:
            unit_details.number_of_unit_members = member_count

        final_member_count = db.execute(
            select(func.count())
            .select_from(UnitMembers)
            .where(UnitMembers.registered_user_id == user_id)
        ).scalar()
        final_councilor_count = db.execute(
            select(func.count())
            .select_from(UnitCouncilor)
            .where(UnitCouncilor.registered_user_id == user_id)
        ).scalar()

        print(f"Seeded successfully:")
        print(f"  Members: {final_member_count}")
        print(f"  Councilors: {final_councilor_count} (required for {member_count} members: {required_councilors})")
        print(f"  Councilor names: {[m.name for m in councilor_candidates]}")


if __name__ == "__main__":
    seed()
