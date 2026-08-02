"""
Setup production database with fresh schema from models.
Bypasses Alembic migrations and creates tables directly from SQLAlchemy models.
"""
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.pool import NullPool

# Production database - use direct connection (not pooler) for DDL operations
# Remove the "-pooler" from hostname for direct connection
PROD_DB_URL = "postgresql://neondb_owner:npg_mcp40TxrFHVC@ep-round-waterfall-a13doc66.ap-southeast-1.aws.neon.tech/csi_youth_db?sslmode=require"

def setup_production():
    print("Setting up production database...")
    
    engine = create_engine(PROD_DB_URL, poolclass=NullPool)
    
    with engine.connect() as conn:
        # First, check what exists
        result = conn.execute(text("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_type = 'BASE TABLE'
        """))
        tables = [row[0] for row in result]
        print(f"Current tables: {tables}")
        
        # Drop and recreate schema
        print("Dropping schema...")
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO neondb_owner"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
        conn.commit()
        print("Schema recreated!")
    
    engine.dispose()
    print("Done!")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--yes":
        setup_production()
    else:
        print("WARNING: This will drop the entire public schema!")
        response = input("Continue? (yes/no): ").strip().lower()
        if response == "yes":
            setup_production()

