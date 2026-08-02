"""
Clean production database - drop schema and recreate.
"""
from sqlalchemy import create_engine, text

PROD_DB_URL = "postgresql://neondb_owner:npg_mcp40TxrFHVC@ep-round-waterfall-a13doc66-pooler.ap-southeast-1.aws.neon.tech/csi_youth_db?sslmode=require"

def clean_database():
    engine = create_engine(PROD_DB_URL)
    
    with engine.connect() as conn:
        print("Checking for objects in database...")
        
        # Check for any remaining objects
        result = conn.execute(text("""
            SELECT 
                n.nspname as schema,
                c.relname as name,
                CASE c.relkind
                    WHEN 'r' THEN 'table'
                    WHEN 'v' THEN 'view'
                    WHEN 'm' THEN 'materialized view'
                    WHEN 'i' THEN 'index'
                    WHEN 'S' THEN 'sequence'
                    WHEN 's' THEN 'special'
                    WHEN 'f' THEN 'foreign table'
                    WHEN 'p' THEN 'partitioned table'
                    WHEN 'I' THEN 'partitioned index'
                END as type
            FROM pg_catalog.pg_class c
            LEFT JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
            ORDER BY type, name
        """))
        
        objects = list(result)
        if objects:
            print(f"Found {len(objects)} objects:")
            for obj in objects:
                print(f"  - {obj[2]}: {obj[1]}")
        
        # Drop and recreate schema
        print("\nDropping and recreating public schema...")
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO neondb_owner"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
        conn.commit()
        
        print("Done - Schema cleaned!")

if __name__ == "__main__":
    import sys
    print("WARNING: This will DROP entire public schema in production!")
    if len(sys.argv) > 1 and sys.argv[1] == "--yes":
        clean_database()
    else:
        response = input("Are you sure? (yes/no): ").strip().lower()
        if response == "yes":
            clean_database()
        else:
            print("Cancelled.")
