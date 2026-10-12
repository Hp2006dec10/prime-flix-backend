from sqlalchemy import text
from app.db.session import engine

with engine.begin() as conn:
    print("Adding ott_providers column if not exists...")
    conn.execute(text("ALTER TABLE movies ADD COLUMN IF NOT EXISTS ott_providers JSON;"))
    conn.execute(text("ALTER TABLE webseries ADD COLUMN IF NOT EXISTS ott_providers JSON;"))
    print("ott_providers column added successfully!")
