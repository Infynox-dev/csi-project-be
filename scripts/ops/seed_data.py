"""Seed database with test data"""
import sys
from sqlalchemy import select
from app.common.security import get_password_hash

from app.common.db import SessionLocal
from app.auth.models import CustomUser, UserType, UnitName, ClergyDistrict, UnitMembers


def seed_database():
    db = SessionLocal()
    try:
        # Check if admin user already exists
        existing_admin = db.execute(
            select(CustomUser).where(CustomUser.username == "admin")
        ).scalar_one_or_none()
        
        if existing_admin:
            print("Admin user already exists")
            return
        
        # Create clergy district
        district = ClergyDistrict(
            id=1,
            name="Test District"
        )
        db.add(district)
        db.flush()
        
        # Create unit name
        unit_name = UnitName(
            id=1,
            clergy_district_id=1,
            name="Test Unit"
        )
        db.add(unit_name)
        db.flush()
        
        # Create admin user (UserType.ADMIN = "1")
        admin = CustomUser(
            email="admin@test.com",
            username="admin",
            hashed_password=get_password_hash("admin"),
            first_name="Admin",
            last_name="User",
            phone_number="1234567890",
            user_type=UserType.ADMIN,
            is_active=True
        )
        db.add(admin)
        
        # Create unit user (UserType.UNIT = "2")
        unit_user = CustomUser(
            email="unit@test.com",
            username="unit",
            hashed_password=get_password_hash("unit"),
            first_name="Unit",
            last_name="User",
            phone_number="1234567892",
            user_type=UserType.UNIT,
            unit_name_id=1,
            clergy_district_id=1,
            is_active=True
        )
        db.add(unit_user)
        
        # Create district official user (UserType.DISTRICT_OFFICIAL = "3")
        official = CustomUser(
            email="official@test.com",
            username="official",
            hashed_password=get_password_hash("official"),
            first_name="Official",
            last_name="User",
            phone_number="1234567891",
            user_type=UserType.DISTRICT_OFFICIAL,
            unit_name_id=1,
            clergy_district_id=1,
            is_active=True
        )
        db.add(official)
        
        db.flush()
        
        # Create a test member for unit_user
        member = UnitMembers(
            registered_user_id=unit_user.id,
            name="Test Member",
            gender="M",
            number="9876543210"
        )
        db.add(member)
        
        db.commit()
        print("Database seeded successfully!")
        print("\nTest credentials:")
        print("  Admin:    username=admin,    password=admin    (UserType=ADMIN/1)")
        print("  Unit:     username=unit,     password=unit     (UserType=UNIT/2)")
        print("  Official: username=official, password=official (UserType=DISTRICT_OFFICIAL/3)")
        
    except Exception as e:
        db.rollback()
        print(f"Error seeding database: {e}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    seed_database()

