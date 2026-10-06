import logging
import httpx
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.db.models import Movie
from app.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TMDB_BASE_URL = "https://api.themoviedb.org/3"
OMDB_BASE_URL = "http://www.omdbapi.com"


def backfill():
    db: Session = SessionLocal()
    try:
        movies = db.query(Movie).all()
        logger.info(f"Backfilling genre and language for {len(movies)} movies in database...")
        
        with httpx.Client(timeout=10.0) as client:
            updated_count = 0
            for movie in movies:
                need_update = False
                
                # Fetch TMDB if tmdb_id exists
                if movie.tmdb_id and (not movie.genre or not movie.language):
                    url = f"{TMDB_BASE_URL}/movie/{movie.tmdb_id}"
                    res = client.get(url, params={"api_key": settings.TMDB_API_KEY})
                    if res.status_code == 200:
                        tmdb_data = res.json()
                        tmdb_genres = [g.get("name") for g in tmdb_data.get("genres", []) if g.get("name")]
                        if tmdb_genres and not movie.genre:
                            movie.genre = ", ".join(tmdb_genres)
                            need_update = True
                        
                        tmdb_langs = [l.get("english_name") or l.get("name") for l in tmdb_data.get("spoken_languages", []) if l.get("name")]
                        if tmdb_langs and not movie.language:
                            movie.language = ", ".join(tmdb_langs)
                            need_update = True

                # Fetch OMDB if imdb_id exists and still missing
                if movie.imdb_id and (not movie.genre or not movie.language):
                    res = client.get(OMDB_BASE_URL, params={"apikey": settings.OMDB_API_KEY, "i": movie.imdb_id})
                    if res.status_code == 200:
                        omdb_data = res.json()
                        if omdb_data.get("Response") == "True":
                            if not movie.genre and omdb_data.get("Genre") and omdb_data.get("Genre") != "N/A":
                                movie.genre = omdb_data.get("Genre")
                                need_update = True
                            if not movie.language and omdb_data.get("Language") and omdb_data.get("Language") != "N/A":
                                movie.language = omdb_data.get("Language")
                                need_update = True

                if need_update:
                    updated_count += 1
                    logger.info(f"Updated Movie #{movie.id} '{movie.title}': Genre='{movie.genre}', Language='{movie.language}'")

            db.commit()
            logger.info(f"Successfully backfilled {updated_count} movies.")
    except Exception as e:
        logger.error(f"Backfill error: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    backfill()
