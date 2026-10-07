import re
import sys
import logging
import httpx
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import engine, SessionLocal, init_db
from app.db.models import Base, Director, Movie

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TMDB_BASE_URL = "https://api.themoviedb.org/3"
OMDB_BASE_URL = "http://www.omdbapi.com"


def parse_int(val: Any) -> Optional[int]:
    if not val:
        return None
    try:
        match = re.search(r"\d+", str(val))
        return int(match.group()) if match else None
    except Exception:
        return None


def parse_float(val: Any) -> Optional[float]:
    if not val:
        return None
    try:
        match = re.search(r"\d+(\.\d+)?", str(val))
        return float(match.group()) if match else None
    except Exception:
        return None


def fetch_tmdb_movie(client: httpx.Client, tmdb_id: int) -> Optional[Dict[str, Any]]:
    url = f"{TMDB_BASE_URL}/movie/{tmdb_id}"
    params = {"api_key": settings.TMDB_API_KEY}
    res = client.get(url, params=params)
    if res.status_code == 200:
        return res.json()
    return None


def fetch_omdb_movie_by_imdb(client: httpx.Client, imdb_id: str) -> Optional[Dict[str, Any]]:
    if not settings.OMDB_API_KEY:
        logger.error("OMDB_API_KEY is not set in settings!")
        return None
    params = {
        "apikey": settings.OMDB_API_KEY,
        "i": imdb_id
    }
    res = client.get(OMDB_BASE_URL, params=params)
    if res.status_code == 200:
        data = res.json()
        if data.get("Response") == "True":
            return data
        else:
            logger.warning(f"OMDB error for IMDB ID {imdb_id}: {data.get('Error')}")
    else:
        logger.error(f"OMDB HTTP request failed for IMDB ID {imdb_id} with status {res.status_code}")
    return None


def fetch_omdb_movie_by_title(client: httpx.Client, title: str, year: Optional[int] = None) -> Optional[Dict[str, Any]]:
    if not settings.OMDB_API_KEY:
        return None
    params = {
        "apikey": settings.OMDB_API_KEY,
        "t": title
    }
    if year:
        params["y"] = str(year)

    res = client.get(OMDB_BASE_URL, params=params)
    if res.status_code == 200:
        data = res.json()
        if data.get("Response") == "True":
            return data
    return None


def get_or_create_director(db: Session, name: str, nationality: Optional[str] = None) -> Director:
    director = db.query(Director).filter(Director.name == name).first()
    if not director:
        director = Director(name=name, nationality=nationality)
        db.add(director)
        db.commit()
        db.refresh(director)
        logger.info(f"Created Director: {name} (ID: {director.id})")
    elif not director.nationality and nationality:
        director.nationality = nationality
        db.commit()
        db.refresh(director)
    return director


def update_missing_movies():
    init_db()
    db: Session = SessionLocal()

    try:
        # Fetch all movies in database where genre or language or director or other fields are missing
        movies = db.query(Movie).all()
        target_movies = [
            m for m in movies
            if not m.genre or not m.language or m.genre.strip() == "" or m.language.strip() == ""
            or m.genre == "N/A" or m.language == "N/A" or not m.director_id
        ]

        logger.info(f"Total movies in DB: {len(movies)}")
        logger.info(f"Movies needing genre/language/director update: {len(target_movies)}")

        if not target_movies:
            logger.info("All movies already have valid genre and language details!")
            return

        updated_count = 0
        with httpx.Client(timeout=15.0) as client:
            for idx, movie in enumerate(target_movies, start=1):
                logger.info(f"[{idx}/{len(target_movies)}] Checking Movie ID {movie.id}: '{movie.title}' (TMDB: {movie.tmdb_id}, IMDB: {movie.imdb_id})")

                omdb_data = None
                tmdb_data = None

                # 1. Try fetching via IMDB ID from OMDB
                if movie.imdb_id:
                    omdb_data = fetch_omdb_movie_by_imdb(client, movie.imdb_id)

                # 2. If no IMDB ID or OMDB failed, try TMDB first to retrieve IMDB ID or TMDB genre/lang
                if not omdb_data and movie.tmdb_id:
                    tmdb_data = fetch_tmdb_movie(client, movie.tmdb_id)
                    if tmdb_data:
                        fetched_imdb_id = tmdb_data.get("imdb_id")
                        if fetched_imdb_id and not movie.imdb_id:
                            movie.imdb_id = fetched_imdb_id
                        if fetched_imdb_id:
                            omdb_data = fetch_omdb_movie_by_imdb(client, fetched_imdb_id)

                # 3. Fallback: Search OMDB by Title
                if not omdb_data and movie.title:
                    omdb_data = fetch_omdb_movie_by_title(client, movie.title, movie.release_year)

                fields_updated = []

                # Populate from OMDB if available
                if omdb_data:
                    # Genre
                    omdb_genre = omdb_data.get("Genre")
                    if omdb_genre and omdb_genre != "N/A" and (not movie.genre or movie.genre == "N/A"):
                        movie.genre = omdb_genre
                        fields_updated.append(f"Genre='{omdb_genre}'")

                    # Language
                    omdb_lang = omdb_data.get("Language")
                    if omdb_lang and omdb_lang != "N/A" and (not movie.language or movie.language == "N/A"):
                        movie.language = omdb_lang
                        fields_updated.append(f"Language='{omdb_lang}'")

                    # Director
                    omdb_dir = omdb_data.get("Director")
                    if omdb_dir and omdb_dir != "N/A" and not movie.director_id:
                        country = omdb_data.get("Country") if omdb_data.get("Country") != "N/A" else None
                        dir_obj = get_or_create_director(db, name=omdb_dir, nationality=country)
                        movie.director_id = dir_obj.id
                        fields_updated.append(f"Director='{omdb_dir}'")

                    # Rating
                    omdb_rating = parse_float(omdb_data.get("imdbRating"))
                    if omdb_rating and not movie.average_rating:
                        movie.average_rating = omdb_rating
                        fields_updated.append(f"Rating={omdb_rating}")

                    # Plot / Description
                    omdb_plot = omdb_data.get("Plot")
                    if omdb_plot and omdb_plot != "N/A" and not movie.description:
                        movie.description = omdb_plot
                        fields_updated.append("Description")

                    # Runtime
                    omdb_runtime = parse_int(omdb_data.get("Runtime"))
                    if omdb_runtime and not movie.duration:
                        movie.duration = omdb_runtime
                        fields_updated.append(f"Duration={omdb_runtime}m")

                    # Poster
                    omdb_poster = omdb_data.get("Poster")
                    if omdb_poster and omdb_poster != "N/A" and not movie.poster_image_url:
                        movie.poster_image_url = omdb_poster
                        fields_updated.append("Poster")

                # Fallback to TMDB genre/language if OMDB didn't have them
                if tmdb_data or (movie.tmdb_id and not tmdb_data):
                    if not tmdb_data and movie.tmdb_id:
                        tmdb_data = fetch_tmdb_movie(client, movie.tmdb_id)

                    if tmdb_data:
                        if not movie.genre or movie.genre == "N/A":
                            genres = [g.get("name") for g in tmdb_data.get("genres", []) if g.get("name")]
                            if genres:
                                movie.genre = ", ".join(genres)
                                fields_updated.append(f"Genre(TMDB)='{movie.genre}'")

                        if not movie.language or movie.language == "N/A":
                            langs = [l.get("english_name") or l.get("name") for l in tmdb_data.get("spoken_languages", []) if l.get("name")]
                            movie.language = ", ".join(langs) if langs else tmdb_data.get("original_language")
                            if movie.language:
                                fields_updated.append(f"Language(TMDB)='{movie.language}'")

                        if not movie.poster_image_url and tmdb_data.get("poster_path"):
                            movie.poster_image_url = f"https://image.tmdb.org/t/p/w500{tmdb_data.get('poster_path')}"
                            fields_updated.append("Poster(TMDB)")

                if fields_updated:
                    db.commit()
                    updated_count += 1
                    logger.info(f"✅ Updated '{movie.title}' (ID {movie.id}): {', '.join(fields_updated)}")
                else:
                    logger.warning(f"⚠️ No new details retrieved for '{movie.title}' (ID {movie.id})")

        logger.info(f"==========================================")
        logger.info(f"Finished updating movies. Total updated: {updated_count}/{len(target_movies)}")
        logger.info(f"==========================================")

    except Exception as e:
        logger.error(f"Error during update process: {e}", exc_info=True)
    finally:
        db.close()


if __name__ == "__main__":
    update_missing_movies()
