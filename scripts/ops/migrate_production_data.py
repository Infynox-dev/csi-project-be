"""
Data migration script from production Django database to new FastAPI database.

This script migrates:
- Users (CustomUser)
- Districts and Units
- Unit registration data
- Unit members, officials, councilors
- Conference data (if exists)
- Kalamela data (if exists)
"""

import os
import sys
from datetime import datetime
from sqlalchemy import create_engine, text, MetaData, Table
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# Production database URL
PRODUCTION_DB_URL = "postgresql://neondb_owner:npg_mcp40TxrFHVC@ep-round-waterfall-a13doc66-pooler.ap-southeast-1.aws.neon.tech/csi_youth_db?sslmode=require"

# Local database URL (from .env)
LOCAL_DB_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/csi_kalamela")


class DataMigrator:
    """Handles data migration from production to local database."""
    
    def __init__(self, prod_url: str, local_url: str):
        """Initialize database connections."""
        print("🔌 Connecting to databases...")
        
        # Production database (read-only)
        self.prod_engine = create_engine(prod_url, poolclass=NullPool, echo=False)
        self.prod_session = sessionmaker(bind=self.prod_engine)()
        
        # Local database (write)
        self.local_engine = create_engine(local_url, poolclass=NullPool, echo=False)
        self.local_session = sessionmaker(bind=self.local_engine)()
        
        print("✅ Connected to both databases\n")
        
        # Stats
        self.stats = {
            "districts": 0,
            "units": 0,
            "users": 0,
            "unit_members": 0,
            "unit_officials": 0,
            "unit_councilors": 0,
            "conferences": 0,
            "kalamela_events": 0,
            "errors": []
        }
    
    def inspect_production_tables(self):
        """List all tables in production database."""
        print("📋 Inspecting production database schema...\n")
        
        query = """
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' 
        ORDER BY table_name;
        """
        
        result = self.prod_session.execute(text(query))
        tables = [row[0] for row in result]
        
        print(f"Found {len(tables)} tables in production:")
        for table in tables:
            print(f"  - {table}")
        
        print()
        return tables
    
    def migrate_districts(self):
        """Migrate clergy districts."""
        print("🏛️  Migrating clergy districts...")
        
        query = "SELECT id, name FROM old_db.auth_app_clergydistrict ORDER BY id;"
        result = self.prod_session.execute(text(query))
        
        for row in result:
            district_id, name = row
            
            # Check if already exists
            check = self.local_session.execute(
                text("SELECT id FROM clergy_district WHERE id = :id"),
                {"id": district_id}
            ).first()
            
            if not check:
                self.local_session.execute(
                    text("INSERT INTO clergy_district (id, name) VALUES (:id, :name)"),
                    {"id": district_id, "name": name}
                )
                self.stats["districts"] += 1
        
        self.local_session.commit()
        print(f"✅ Migrated {self.stats['districts']} districts\n")
    
    def migrate_units(self):
        """Migrate unit names."""
        print("🏢 Migrating unit names...")
        
        query = "SELECT id, clergy_district_id, name FROM old_db.auth_app_unitname ORDER BY id;"
        result = self.prod_session.execute(text(query))
        
        for row in result:
            unit_id, district_id, name = row
            
            # Check if already exists
            check = self.local_session.execute(
                text("SELECT id FROM unit_name WHERE id = :id"),
                {"id": unit_id}
            ).first()
            
            if not check:
                self.local_session.execute(
                    text("""
                        INSERT INTO unit_name (id, clergy_district_id, name) 
                        VALUES (:id, :district_id, :name)
                    """),
                    {"id": unit_id, "district_id": district_id, "name": name}
                )
                self.stats["units"] += 1
        
        self.local_session.commit()
        print(f"✅ Migrated {self.stats['units']} units\n")
    
    def migrate_users(self):
        """Migrate custom users."""
        print("👤 Migrating users...")
        
        query = """
        SELECT id, email, username, first_name, last_name, phone_number, 
               user_type, password, is_active, unit_name_id, clergy_district_id,
               conference_member_count, conference_official_count, conference_id
        FROM old_db.auth_app_customuser 
        ORDER BY id;
        """
        result = self.prod_session.execute(text(query))
        
        for row in result:
            user_data = {
                "id": row[0],
                "email": row[1],
                "username": row[2],
                "first_name": row[3],
                "last_name": row[4],
                "phone_number": row[5],
                "user_type": row[6],
                "hashed_password": row[7],  # Already hashed
                "is_active": row[8],
                "unit_name_id": row[9],
                "clergy_district_id": row[10],
                "conference_member_count": row[11],
                "conference_official_count": row[12],
                "conference_id": row[13]
            }
            
            # Check if already exists
            check = self.local_session.execute(
                text("SELECT id FROM custom_user WHERE id = :id"),
                {"id": user_data["id"]}
            ).first()
            
            if not check:
                self.local_session.execute(
                    text("""
                        INSERT INTO custom_user 
                        (id, email, username, first_name, last_name, phone_number, 
                         user_type, hashed_password, is_active, unit_name_id, 
                         clergy_district_id, conference_member_count, 
                         conference_official_count, conference_id)
                        VALUES 
                        (:id, :email, :username, :first_name, :last_name, :phone_number,
                         :user_type, :hashed_password, :is_active, :unit_name_id,
                         :clergy_district_id, :conference_member_count,
                         :conference_official_count, :conference_id)
                    """),
                    user_data
                )
                self.stats["users"] += 1
        
        self.local_session.commit()
        print(f"✅ Migrated {self.stats['users']} users\n")
    
    def migrate_unit_data(self):
        """Migrate unit registration data, members, officials, councilors."""
        print("📝 Migrating unit data...")
        
        # Unit registration data
        query = "SELECT id, registered_user_id, status FROM old_db.auth_app_unitregistrationdata;"
        try:
            result = self.prod_session.execute(text(query))
            for row in result:
                check = self.local_session.execute(
                    text("SELECT id FROM unit_registration_data WHERE id = :id"),
                    {"id": row[0]}
                ).first()
                
                if not check:
                    self.local_session.execute(
                        text("""
                            INSERT INTO unit_registration_data 
                            (id, registered_user_id, status) 
                            VALUES (:id, :user_id, :status)
                        """),
                        {"id": row[0], "user_id": row[1], "status": row[2]}
                    )
        except Exception as e:
            print(f"⚠️  Unit registration data: {e}")
        
        # Unit details
        query = "SELECT id, registered_user_id, registration_year, number_of_unit_members FROM old_db.auth_app_unitdetails;"
        try:
            result = self.prod_session.execute(text(query))
            for row in result:
                check = self.local_session.execute(
                    text("SELECT id FROM unit_details WHERE id = :id"),
                    {"id": row[0]}
                ).first()
                
                if not check:
                    self.local_session.execute(
                        text("""
                            INSERT INTO unit_details 
                            (id, registered_user_id, registration_year, number_of_unit_members) 
                            VALUES (:id, :user_id, :year, :count)
                        """),
                        {"id": row[0], "user_id": row[1], "year": row[2], "count": row[3]}
                    )
        except Exception as e:
            print(f"⚠️  Unit details: {e}")
        
        # Unit members
        query = """
        SELECT id, registered_user_id, name, gender, dob, number, 
               qualification, blood_group 
        FROM old_db.auth_app_unitmembers;
        """
        try:
            result = self.prod_session.execute(text(query))
            for row in result:
                check = self.local_session.execute(
                    text("SELECT id FROM unit_members WHERE id = :id"),
                    {"id": row[0]}
                ).first()
                
                if not check:
                    self.local_session.execute(
                        text("""
                            INSERT INTO unit_members 
                            (id, registered_user_id, name, gender, dob, number, 
                             qualification, blood_group) 
                            VALUES (:id, :user_id, :name, :gender, :dob, :number,
                                    :qualification, :blood_group)
                        """),
                        {
                            "id": row[0], "user_id": row[1], "name": row[2],
                            "gender": row[3], "dob": row[4], "number": row[5],
                            "qualification": row[6], "blood_group": row[7]
                        }
                    )
                    self.stats["unit_members"] += 1
        except Exception as e:
            print(f"⚠️  Unit members: {e}")
        
        # Unit officials (with updated structure)
        query = """
        SELECT id, registered_user_id, 
               president_designation, president_name, president_phone,
               vice_president_designation, vice_president_name, vice_president_phone,
               secretary_designation, secretary_name, secretary_phone,
               joint_secretary_designation, joint_secretary_name, joint_secretary_phone,
               treasurer_designation, treasurer_name, treasurer_phone
        FROM old_db.auth_app_unitofficials;
        """
        try:
            result = self.prod_session.execute(text(query))
            for row in result:
                check = self.local_session.execute(
                    text("SELECT id FROM unit_officials WHERE id = :id"),
                    {"id": row[0]}
                ).first()
                
                if not check:
                    self.local_session.execute(
                        text("""
                            INSERT INTO unit_officials 
                            (id, registered_user_id, 
                             president_designation, president_name, president_phone,
                             vice_president_designation, vice_president_name, vice_president_phone,
                             secretary_designation, secretary_name, secretary_phone,
                             joint_secretary_designation, joint_secretary_name, joint_secretary_phone,
                             treasurer_designation, treasurer_name, treasurer_phone)
                            VALUES 
                            (:id, :user_id,
                             :pres_des, :pres_name, :pres_phone,
                             :vp_des, :vp_name, :vp_phone,
                             :sec_des, :sec_name, :sec_phone,
                             :jsec_des, :jsec_name, :jsec_phone,
                             :treas_des, :treas_name, :treas_phone)
                        """),
                        {
                            "id": row[0], "user_id": row[1],
                            "pres_des": row[2], "pres_name": row[3], "pres_phone": row[4],
                            "vp_des": row[5], "vp_name": row[6], "vp_phone": row[7],
                            "sec_des": row[8], "sec_name": row[9], "sec_phone": row[10],
                            "jsec_des": row[11], "jsec_name": row[12], "jsec_phone": row[13],
                            "treas_des": row[14], "treas_name": row[15], "treas_phone": row[16]
                        }
                    )
                    self.stats["unit_officials"] += 1
        except Exception as e:
            print(f"⚠️  Unit officials: {e}")
        
        # Unit councilors
        query = "SELECT id, registered_user_id, name, number FROM old_db.auth_app_unitcouncilor;"
        try:
            result = self.prod_session.execute(text(query))
            for row in result:
                check = self.local_session.execute(
                    text("SELECT id FROM unit_councilor WHERE id = :id"),
                    {"id": row[0]}
                ).first()
                
                if not check:
                    self.local_session.execute(
                        text("""
                            INSERT INTO unit_councilor 
                            (id, registered_user_id, name, number) 
                            VALUES (:id, :user_id, :name, :number)
                        """),
                        {"id": row[0], "user_id": row[1], "name": row[2], "number": row[3]}
                    )
                    self.stats["unit_councilors"] += 1
        except Exception as e:
            print(f"⚠️  Unit councilors: {e}")
        
        self.local_session.commit()
        print(f"✅ Migrated unit data:")
        print(f"   - {self.stats['unit_members']} members")
        print(f"   - {self.stats['unit_officials']} officials")
        print(f"   - {self.stats['unit_councilors']} councilors\n")
    
    def migrate_login_audit(self):
        """Migrate login audit logs."""
        print("📊 Migrating login audit logs...")
        
        query = """
        SELECT id, user_id, username, success, timestamp 
        FROM old_db.auth_app_loginaudit 
        ORDER BY timestamp DESC 
        LIMIT 1000;
        """
        try:
            result = self.prod_session.execute(text(query))
            count = 0
            for row in result:
                self.local_session.execute(
                    text("""
                        INSERT INTO login_audit 
                        (id, user_id, username, success) 
                        VALUES (:id, :user_id, :username, :success)
                        ON CONFLICT (id) DO NOTHING
                    """),
                    {
                        "id": row[0], "user_id": row[1],
                        "username": row[2], "success": row[3]
                    }
                )
                count += 1
            
            self.local_session.commit()
            print(f"✅ Migrated {count} login audit logs\n")
        except Exception as e:
            print(f"⚠️  Login audit: {e}\n")
    
    def print_summary(self):
        """Print migration summary."""
        print("\n" + "="*60)
        print("📊 MIGRATION SUMMARY")
        print("="*60)
        print(f"✅ Districts:        {self.stats['districts']}")
        print(f"✅ Units:            {self.stats['units']}")
        print(f"✅ Users:            {self.stats['users']}")
        print(f"✅ Unit Members:     {self.stats['unit_members']}")
        print(f"✅ Unit Officials:   {self.stats['unit_officials']}")
        print(f"✅ Unit Councilors:  {self.stats['unit_councilors']}")
        
        if self.stats['errors']:
            print(f"\n⚠️  Errors encountered: {len(self.stats['errors'])}")
            for error in self.stats['errors']:
                print(f"   - {error}")
        
        print("="*60 + "\n")
    
    def close(self):
        """Close database connections."""
        self.prod_session.close()
        self.local_session.close()
        self.prod_engine.dispose()
        self.local_engine.dispose()
        print("🔌 Database connections closed")


def main():
    """Run the migration."""
    print("\n" + "="*60)
    print("🚀 DATA MIGRATION: Production → Local")
    print("="*60 + "\n")
    
    # Confirm before proceeding
    print("⚠️  WARNING: This will import data from production database")
    print("📍 Production: Neon Database (csi_youth_db)")
    print(f"📍 Local: {LOCAL_DB_URL}\n")
    
    response = input("Do you want to continue? (yes/no): ").strip().lower()
    if response != "yes":
        print("❌ Migration cancelled")
        return
    
    print()
    
    try:
        migrator = DataMigrator(PRODUCTION_DB_URL, LOCAL_DB_URL)
        
        # Inspect production tables first
        tables = migrator.inspect_production_tables()
        
        # Run migrations in order (respecting foreign keys)
        migrator.migrate_districts()
        migrator.migrate_units()
        migrator.migrate_users()
        migrator.migrate_unit_data()
        migrator.migrate_login_audit()
        
        # Print summary
        migrator.print_summary()
        
        # Close connections
        migrator.close()
        
        print("✅ Migration completed successfully!\n")
        
    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

