"""Inspect production database schema to understand table structure."""

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.pool import NullPool

PRODUCTION_DB_URL = "postgresql://neondb_owner:npg_mcp40TxrFHVC@ep-round-waterfall-a13doc66-pooler.ap-southeast-1.aws.neon.tech/csi_youth_db?sslmode=require"

print("🔍 Inspecting production database schema...\n")

try:
    engine = create_engine(PRODUCTION_DB_URL, poolclass=NullPool)
    inspector = inspect(engine)
    
    # Get all schemas
    schemas = inspector.get_schema_names()
    print(f"📂 Available schemas: {schemas}\n")
    
    # Check each schema for tables
    for schema in schemas:
        tables = inspector.get_table_names(schema=schema)
        if tables:
            print(f"\n📋 Tables in '{schema}' schema ({len(tables)} tables):")
            for table in sorted(tables)[:20]:  # Show first 20
                print(f"   - {table}")
                
                # Show column count
                columns = inspector.get_columns(table, schema=schema)
                print(f"     ({len(columns)} columns)")
    
    # Get row counts for key tables
    print("\n📊 Row counts for key tables:\n")
    
    with engine.connect() as conn:
        tables_to_check = [
            "auth_app_customuser",
            "auth_app_clergydistrict",
            "auth_app_unitname",
            "auth_app_unitmembers",
            "auth_app_unitofficials",
            "auth_app_unitcouncilor",
            "auth_app_unitregistrationdata",
        ]
        
        for table in tables_to_check:
            try:
                result = conn.execute(text(f"SELECT COUNT(*) FROM {table};"))
                count = result.scalar()
                print(f"   {table:40s}: {count:6d} rows")
            except Exception as e:
                print(f"   {table:40s}: ⚠️  Not found or error")
    
    engine.dispose()
    print("\n✅ Inspection complete!\n")

except Exception as e:
    print(f"\n❌ Inspection failed: {e}\n")


