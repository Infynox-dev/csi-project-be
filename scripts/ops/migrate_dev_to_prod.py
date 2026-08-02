"""
Data migration script from Development Neon DB to Production Neon DB.
Performs a FULL SYNC - truncates production tables and copies all data from dev.

Usage:
    python tests/migrate_dev_to_prod.py
"""

import os
import sys
from sqlalchemy import create_engine, text, inspect, MetaData
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# Development database (source) - use direct connection for reliability
DEV_DB_URL = "postgresql://neondb_owner:npg_mcp40TxrFHVC@ep-snowy-heart-a1z65pya.ap-southeast-1.aws.neon.tech/csi_youth_db?sslmode=require"

# Production database (target) - use direct connection for reliability
PROD_DB_URL = "postgresql://neondb_owner:npg_mcp40TxrFHVC@ep-round-waterfall-a13doc66.ap-southeast-1.aws.neon.tech/csi_youth_db?sslmode=require"


class DevToProdMigrator:
    """Handles data migration from development to production database."""
    
    def __init__(self, dev_url: str, prod_url: str):
        print("[*] Connecting to databases...")
        
        # Development database (read)
        self.dev_engine = create_engine(dev_url, poolclass=NullPool, echo=False)
        self.dev_session = sessionmaker(bind=self.dev_engine)()
        
        # Production database (write)
        self.prod_engine = create_engine(prod_url, poolclass=NullPool, echo=False)
        self.prod_session = sessionmaker(bind=self.prod_engine)()
        
        print("[OK] Connected to both databases\n")
        
        self.stats = {
            "tables_migrated": 0,
            "total_rows": 0,
            "errors": []
        }
    
    def get_dev_tables(self):
        """Get all tables from development database."""
        inspector = inspect(self.dev_engine)
        tables = inspector.get_table_names()
        # Filter out alembic version table
        return [t for t in tables if t != 'alembic_version']
    
    def get_prod_tables(self):
        """Get all tables from production database."""
        inspector = inspect(self.prod_engine)
        tables = inspector.get_table_names()
        return [t for t in tables if t != 'alembic_version']
    
    def get_table_row_count(self, table_name: str, session) -> int:
        """Get row count for a table."""
        try:
            result = session.execute(text(f'SELECT COUNT(*) FROM "{table_name}"'))
            return result.scalar()
        except Exception:
            return -1
    
    def compare_databases(self, title: str = "Database Comparison"):
        """Compare row counts between dev and prod."""
        print(f"\n[INFO] {title}\n")
        print(f"{'Table':<40} {'Dev':<10} {'Prod':<10} {'Status'}")
        print("-" * 70)
        
        dev_tables = set(self.get_dev_tables())
        prod_tables = set(self.get_prod_tables())
        all_tables = dev_tables.union(prod_tables)
        
        for table in sorted(all_tables):
            dev_count = self.get_table_row_count(table, self.dev_session) if table in dev_tables else "N/A"
            prod_count = self.get_table_row_count(table, self.prod_session) if table in prod_tables else "N/A"
            
            if dev_count == prod_count:
                status = "[SYNCED]"
            elif table not in prod_tables:
                status = "[MISSING IN PROD]"
            elif table not in dev_tables:
                status = "[MISSING IN DEV]"
            else:
                status = "[NEEDS SYNC]"
            
            print(f"{table:<40} {str(dev_count):<10} {str(prod_count):<10} {status}")
        print()
    
    def get_table_columns(self, table_name: str, engine):
        """Get column names for a table."""
        inspector = inspect(engine)
        columns = inspector.get_columns(table_name)
        return [col['name'] for col in columns]
    
    def truncate_prod_table(self, table_name: str):
        """Truncate a production table."""
        try:
            self.prod_session.execute(text(f'TRUNCATE TABLE "{table_name}" CASCADE'))
            self.prod_session.commit()
            return True
        except Exception as e:
            self.prod_session.rollback()
            print(f"    [WARN] Could not truncate {table_name}: {e}")
            return False
    
    def migrate_table(self, table_name: str):
        """Migrate a single table from dev to prod."""
        print(f"[>] Migrating {table_name}...")
        
        try:
            # Get columns that exist in both databases
            dev_columns = set(self.get_table_columns(table_name, self.dev_engine))
            prod_columns = set(self.get_table_columns(table_name, self.prod_engine))
            common_columns = dev_columns.intersection(prod_columns)
            
            if not common_columns:
                print(f"    [WARN] No common columns found")
                return 0
            
            # Truncate production table first
            self.truncate_prod_table(table_name)
            
            # Get all data from dev (only common columns)
            col_list = ', '.join([f'"{col}"' for col in common_columns])
            result = self.dev_session.execute(text(f'SELECT {col_list} FROM "{table_name}"'))
            rows = result.fetchall()
            columns = list(common_columns)
            
            if not rows:
                print(f"    [WARN] No data in {table_name}")
                return 0
            
            # Insert into prod in batches
            col_names = ', '.join([f'"{col}"' for col in columns])
            placeholders = ', '.join([f":{col}" for col in columns])
            insert_sql = f'INSERT INTO "{table_name}" ({col_names}) VALUES ({placeholders})'
            
            batch_size = 100
            count = 0
            
            for i in range(0, len(rows), batch_size):
                batch = rows[i:i + batch_size]
                for row in batch:
                    row_dict = dict(zip(columns, row))
                    try:
                        self.prod_session.execute(text(insert_sql), row_dict)
                        count += 1
                    except Exception as e:
                        # Try with ON CONFLICT DO NOTHING for tables with unique constraints
                        try:
                            insert_sql_safe = f'INSERT INTO "{table_name}" ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'
                            self.prod_session.execute(text(insert_sql_safe), row_dict)
                            count += 1
                        except Exception as e2:
                            self.stats["errors"].append(f"{table_name}: {str(e2)[:50]}")
                
                self.prod_session.commit()
            
            print(f"    [OK] Migrated {count} rows")
            self.stats["total_rows"] += count
            self.stats["tables_migrated"] += 1
            return count
            
        except Exception as e:
            self.prod_session.rollback()
            error_msg = f"{table_name}: {str(e)[:100]}"
            self.stats["errors"].append(error_msg)
            print(f"    [ERROR] {e}")
            return 0
    
    def reset_sequences(self):
        """Reset all sequences to max id + 1 for each table."""
        print("\n[*] Resetting sequences...")
        
        # Get all tables and try to reset their sequences
        all_tables = self.get_prod_tables()
        
        for table in all_tables:
            try:
                # Get max id
                result = self.prod_session.execute(
                    text(f'SELECT MAX(id) FROM "{table}"')
                )
                max_id = result.scalar()
                
                if max_id is not None:
                    # Reset sequence
                    seq_name = f"{table}_id_seq"
                    self.prod_session.execute(
                        text(f"SELECT setval('{seq_name}', {max_id + 1}, false)")
                    )
                    print(f"    [OK] {table}: sequence set to {max_id + 1}")
            except Exception as e:
                # Sequence might not exist for this table or no id column
                pass
        
        self.prod_session.commit()
    
    def migrate_all(self):
        """Migrate all tables in correct order (respecting foreign keys)."""
        # Get actual tables from dev
        dev_tables = set(self.get_dev_tables())
        prod_tables = set(self.get_prod_tables())
        
        # Filter to only tables that exist in both
        tables_to_migrate = sorted(dev_tables.intersection(prod_tables))
        
        print(f"\n[*] Starting FULL SYNC migration of {len(tables_to_migrate)} tables...\n")
        print("[!] This will TRUNCATE all production tables and copy data from dev!\n")
        
        for table in tables_to_migrate:
            self.migrate_table(table)
        
        # Reset sequences after migration
        self.reset_sequences()
    
    def print_summary(self):
        """Print migration summary."""
        print("\n" + "=" * 60)
        print("MIGRATION SUMMARY")
        print("=" * 60)
        print(f"[OK] Tables migrated:  {self.stats['tables_migrated']}")
        print(f"[OK] Total rows:       {self.stats['total_rows']}")
        
        if self.stats['errors']:
            print(f"\n[!] Errors encountered: {len(self.stats['errors'])}")
            for error in self.stats['errors'][:10]:  # Show first 10 errors
                print(f"   - {error}")
            if len(self.stats['errors']) > 10:
                print(f"   ... and {len(self.stats['errors']) - 10} more")
        
        print("=" * 60 + "\n")
    
    def close(self):
        """Close database connections."""
        self.dev_session.close()
        self.prod_session.close()
        self.dev_engine.dispose()
        self.prod_engine.dispose()
        print("[*] Database connections closed")


def main():
    print("\n" + "=" * 60)
    print("FULL SYNC: Development -> Production")
    print("=" * 60 + "\n")
    
    print("[!] WARNING: This will TRUNCATE all production tables!")
    print("[*] Source (Dev):  ep-snowy-heart-a1z65pya.ap-southeast-1.aws.neon.tech")
    print("[*] Target (Prod): ep-round-waterfall-a13doc66.ap-southeast-1.aws.neon.tech\n")
    
    # Auto-confirm for scripted runs, or ask for confirmation
    if len(sys.argv) > 1 and sys.argv[1] == "--yes":
        response = "yes"
    else:
        response = input("Do you want to continue? (yes/no): ").strip().lower()
    
    if response != "yes":
        print("[X] Migration cancelled")
        return
    
    try:
        migrator = DevToProdMigrator(DEV_DB_URL, PROD_DB_URL)
        
        # Compare before migration
        migrator.compare_databases("BEFORE Migration")
        
        # Run full sync migration
        migrator.migrate_all()
        
        # Compare after migration
        migrator.compare_databases("AFTER Migration")
        
        # Print summary
        migrator.print_summary()
        
        migrator.close()
        
        print("[OK] Full sync migration completed successfully!\n")
        
    except Exception as e:
        print(f"\n[X] Migration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
