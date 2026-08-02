"""Test connection to production database."""

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

PRODUCTION_DB_URL = "postgresql://neondb_owner:npg_mcp40TxrFHVC@ep-round-waterfall-a13doc66-pooler.ap-southeast-1.aws.neon.tech/csi_youth_db?sslmode=require"

print("🔌 Testing connection to production database...\n")
print("Database: Neon (csi_youth_db)")
print("Host: ep-round-waterfall-a13doc66-pooler.ap-southeast-1.aws.neon.tech\n")

try:
    # Create engine
    engine = create_engine(PRODUCTION_DB_URL, poolclass=NullPool)
    
    # Test connection
    with engine.connect() as conn:
        # Get PostgreSQL version
        result = conn.execute(text("SELECT version();"))
        version = result.scalar()
        print(f"✅ Connected successfully!")
        print(f"📊 PostgreSQL Version: {version[:50]}...\n")
        
        # Count tables
        result = conn.execute(text("""
            SELECT COUNT(*) 
            FROM information_schema.tables 
            WHERE table_schema = 'public';
        """))
        table_count = result.scalar()
        print(f"📋 Tables in database: {table_count}")
        
        # Count users
        try:
            result = conn.execute(text("SELECT COUNT(*) FROM auth_app_customuser;"))
            user_count = result.scalar()
            print(f"👤 Total users: {user_count}")
        except Exception as e:
            print(f"⚠️  Could not count users: {e}")
        
        # Count districts
        try:
            result = conn.execute(text("SELECT COUNT(*) FROM auth_app_clergydistrict;"))
            district_count = result.scalar()
            print(f"🏛️  Total districts: {district_count}")
        except Exception as e:
            print(f"⚠️  Could not count districts: {e}")
        
        # Count units
        try:
            result = conn.execute(text("SELECT COUNT(*) FROM auth_app_unitname;"))
            unit_count = result.scalar()
            print(f"🏢 Total units: {unit_count}")
        except Exception as e:
            print(f"⚠️  Could not count units: {e}")
        
        # List some tables
        result = conn.execute(text("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            ORDER BY table_name 
            LIMIT 10;
        """))
        tables = [row[0] for row in result]
        print(f"\n📁 Sample tables:")
        for table in tables:
            print(f"   - {table}")
        
        print("\n✅ Connection test successful!")
        print("🚀 You can now run the migration script\n")
    
    engine.dispose()

except Exception as e:
    print(f"\n❌ Connection failed: {e}")
    print("\nTroubleshooting:")
    print("1. Check if the database credentials are correct")
    print("2. Verify network connectivity to Neon")
    print("3. Ensure SSL requirements are met")
    print()


