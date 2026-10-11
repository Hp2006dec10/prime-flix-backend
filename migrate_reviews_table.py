from sqlalchemy import text
from app.db.session import engine

with engine.begin() as conn:
    print("Creating reviews table if not exists...")
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS reviews (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            content_id VARCHAR(50) NOT NULL,
            content_type VARCHAR(50) NOT NULL DEFAULT 'movie',
            rating FLOAT NOT NULL,
            review_title VARCHAR(255),
            review_text TEXT,
            user_genre_percentages JSON,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
        );
        CREATE INDEX IF NOT EXISTS idx_reviews_user_id ON reviews(user_id);
        CREATE INDEX IF NOT EXISTS idx_reviews_content_id ON reviews(content_id);
    """))
    print("reviews table created successfully!")
