from sqlalchemy import text
from app.db.session import engine

with engine.begin() as conn:
    print("Testing connection and adding columns...")
    try:
        conn.execute(text("ALTER TABLE movies ADD COLUMN IF NOT EXISTS genre_percentages JSON;"))
        print("Added genre_percentages to movies")
    except Exception as e:
        print("movies error:", e)
    
    try:
        conn.execute(text("ALTER TABLE webseries ADD COLUMN IF NOT EXISTS genre_percentages JSON;"))
        print("Added genre_percentages to webseries")
    except Exception as e:
        print("webseries error:", e)

print("Migration completed.")
