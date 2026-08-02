"""Check production database tables."""
from sqlalchemy import create_engine, text

PROD_DB_URL = "postgresql://neondb_owner:npg_mcp40TxrFHVC@ep-round-waterfall-a13doc66-pooler.ap-southeast-1.aws.neon.tech/csi_youth_db?sslmode=require"

engine = create_engine(PROD_DB_URL)
with engine.connect() as conn:
    result = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"))
    tables = [row[0] for row in result]
    print(f'Production tables: {len(tables)}')
    for t in tables:
        print(f'  - {t}')

