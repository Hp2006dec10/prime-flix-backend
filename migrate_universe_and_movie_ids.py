import logging
from sqlalchemy import text
from app.db.session import engine, init_db, SessionLocal
from app.db.models import CinematicUniverse, UniverseContent, Movie

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def migrate_movie_ids_and_universes():
    logger.info("Initializing DB tables...")
    init_db()

    with engine.begin() as conn:
        # Check current data type of movies.id in PostgreSQL
        res = conn.execute(text("""
            SELECT data_type 
            FROM information_schema.columns 
            WHERE table_name = 'movies' AND column_name = 'id';
        """)).fetchone()

        if res and ("int" in res[0].lower()):
            logger.info(f"Existing movies.id column is '{res[0]}'. Converting to VARCHAR(50) with 'MOV-' prefix...")
            try:
                conn.execute(text("ALTER TABLE movies ALTER COLUMN id DROP DEFAULT;"))
                conn.execute(text("ALTER TABLE movies ALTER COLUMN id TYPE VARCHAR(50) USING ('MOV-' || id::text);"))
                logger.info("✅ Successfully converted movies.id column to VARCHAR(50)!")
            except Exception as e:
                logger.error(f"Failed to alter movies.id column: {e}")
        else:
            logger.info(f"movies.id column is already '{res[0] if res else 'unknown'}'. No column alter needed.")

    # Populate initial sample Cinematic Universes if empty
    db = SessionLocal()
    try:
        existing_universes_count = db.query(CinematicUniverse).count()
        if existing_universes_count == 0:
            logger.info("Creating initial sample Cinematic Universes...")
            mcu = CinematicUniverse(
                name="Marvel Cinematic Universe",
                description="A shared universe centered on a series of superhero films independently produced by Marvel Studios."
            )
            dcu = CinematicUniverse(
                name="DC Extended Universe",
                description="An American media franchise and shared universe centered on a series of superhero films produced by DC Studios."
            )
            nolan_verse = CinematicUniverse(
                name="Christopher Nolan Universe",
                description="Collection of groundbreaking cinematic masterpieces directed by Christopher Nolan."
            )
            db.add_all([mcu, dcu, nolan_verse])
            db.commit()
            db.refresh(mcu)
            db.refresh(dcu)
            db.refresh(nolan_verse)

            # Link some sample movies if present
            sample_movies = db.query(Movie).limit(5).all()
            for idx, movie in enumerate(sample_movies):
                uc = UniverseContent(
                    universe_id=nolan_verse.id if idx % 2 == 0 else mcu.id,
                    content_id=str(movie.id),
                    content_type="movie",
                    order_in_universe=idx + 1
                )
                db.add(uc)
            db.commit()
            logger.info("✅ Sample Cinematic Universes & Content relationships created!")

    except Exception as e:
        logger.error(f"Error seeding universes: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    migrate_movie_ids_and_universes()
