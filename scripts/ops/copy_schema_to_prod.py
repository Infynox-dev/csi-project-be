"""
Copy schema from development to production database.
Uses pg_dump style approach with SQLAlchemy.
"""
from sqlalchemy import create_engine, text, inspect, MetaData
from sqlalchemy.pool import NullPool
import sys

# Development database (source) - use direct connection
DEV_DB_URL = "postgresql://neondb_owner:npg_mcp40TxrFHVC@ep-snowy-heart-a1z65pya.ap-southeast-1.aws.neon.tech/csi_youth_db?sslmode=require"

# Production database (target) - use direct connection
PROD_DB_URL = "postgresql://neondb_owner:npg_mcp40TxrFHVC@ep-round-waterfall-a13doc66.ap-southeast-1.aws.neon.tech/csi_youth_db?sslmode=require"


def copy_schema():
    print("Copying schema from development to production...")
    
    dev_engine = create_engine(DEV_DB_URL, poolclass=NullPool)
    prod_engine = create_engine(PROD_DB_URL, poolclass=NullPool)
    
    # Reflect the development schema
    print("Reflecting development schema...")
    dev_metadata = MetaData()
    dev_metadata.reflect(bind=dev_engine)
    
    dev_tables = list(dev_metadata.tables.keys())
    print(f"Found {len(dev_tables)} tables in development:")
    for t in sorted(dev_tables):
        print(f"  - {t}")
    
    # Clean production schema
    print("\nCleaning production schema...")
    with prod_engine.connect() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO neondb_owner"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
        conn.commit()
    
    # Create all tables in production
    print("\nCreating tables in production...")
    dev_metadata.create_all(bind=prod_engine)
    
    # Verify
    prod_metadata = MetaData()
    prod_metadata.reflect(bind=prod_engine)
    prod_tables = list(prod_metadata.tables.keys())
    print(f"\nCreated {len(prod_tables)} tables in production:")
    for t in sorted(prod_tables):
        print(f"  - {t}")
    
    dev_engine.dispose()
    prod_engine.dispose()
    
    print("\nSchema copy complete!")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--yes":
        copy_schema()
    else:
        print("This will copy the schema from development to production.")
        print("WARNING: Production schema will be completely replaced!")
        response = input("Continue? (yes/no): ").strip().lower()
        if response == "yes":
            copy_schema()
        else:
            print("Cancelled.")

