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
        # Extract digits from string e.g. "148 min" -> 148 or "2010" -> 2010
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
    elif res.status_code == 404:
        logger.warning(f"TMDB Movie ID {tmdb_id} not found (404). Skipping.")
    else:
        logger.error(f"TMDB Movie ID {tmdb_id} returned status code {res.status_code}")
    return None


def fetch_tmdb_director(client: httpx.Client, tmdb_id: int) -> Dict[str, Optional[str]]:
    """
    Fetches credits for the TMDB movie to find director name and nationality/place of birth.
    """
    url = f"{TMDB_BASE_URL}/movie/{tmdb_id}/credits"
    params = {"api_key": settings.TMDB_API_KEY}
    res = client.get(url, params=params)
    director_name = None
    nationality = None
    
    if res.status_code == 200:
        credits_data = res.json()
        crew = credits_data.get("crew", [])
        for person in crew:
            if person.get("job") == "Director":
                director_name = person.get("name")
                person_id = person.get("id")
                if person_id:
                    person_url = f"{TMDB_BASE_URL}/person/{person_id}"
                    p_res = client.get(person_url, params=params)
                    if p_res.status_code == 200:
                        p_data = p_res.json()
                        nationality = p_data.get("place_of_birth")
                break

    return {"name": director_name, "nationality": nationality}


def fetch_omdb_movie(client: httpx.Client, imdb_id: str) -> Optional[Dict[str, Any]]:
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
        logger.error(f"OMDB request failed for IMDB ID {imdb_id} with status {res.status_code}")
    return None


def get_or_create_director(db: Session, name: str, nationality: Optional[str] = None) -> Director:
    director = db.query(Director).filter(Director.name == name).first()
    if not director:
        director = Director(name=name, nationality=nationality)
        db.add(director)
        db.commit()
        db.refresh(director)
        logger.info(f"Created new Director: {name} (ID: {director.id}, Nationality: {nationality})")
    else:
        # Update nationality if missing and now available
        if not director.nationality and nationality:
            director.nationality = nationality
            db.commit()
            db.refresh(director)
    return director


def process_movie_id(client: httpx.Client, db: Session, tmdb_id: int):
    logger.info(f"--- Fetching details for TMDB Movie ID: {tmdb_id} ---")
    tmdb_data = fetch_tmdb_movie(client, tmdb_id)
    if not tmdb_data:
        return

    imdb_id = tmdb_data.get("imdb_id")
    tmdb_title = tmdb_data.get("title") or tmdb_data.get("original_title")
    
    # Defaults from TMDB
    title = tmdb_title
    avg_rating = parse_float(tmdb_data.get("vote_average"))
    description = tmdb_data.get("overview")
    duration = parse_int(tmdb_data.get("runtime"))
    release_date = tmdb_data.get("release_date")
    release_year = parse_int(release_date[:4]) if release_date and len(release_date) >= 4 else None

    # Extract Genre and Language from TMDB
    tmdb_genres = [g.get("name") for g in tmdb_data.get("genres", []) if g.get("name")]
    genre = ", ".join(tmdb_genres) if tmdb_genres else None

    tmdb_langs = [l.get("english_name") or l.get("name") for l in tmdb_data.get("spoken_languages", []) if l.get("name")]
    language = ", ".join(tmdb_langs) if tmdb_langs else tmdb_data.get("original_language")

    # Poster image URL from TMDB or OMDB
    poster_path = tmdb_data.get("poster_path")
    poster_image_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None

    director_info = fetch_tmdb_director(client, tmdb_id)
    director_name = director_info.get("name")
    director_nationality = director_info.get("nationality")

    # If IMDB ID exists, call OMDB for exact movie details
    omdb_data = None
    if imdb_id:
        logger.info(f"Found IMDB ID {imdb_id}. Fetching exact details from OMDB API...")
        omdb_data = fetch_omdb_movie(client, imdb_id)

    if not omdb_data and title:
        # Fallback to search OMDB by title
        params = {"apikey": settings.OMDB_API_KEY, "t": title}
        if release_year:
            params["y"] = str(release_year)
        o_res = client.get(OMDB_BASE_URL, params=params)
        if o_res.status_code == 200 and o_res.json().get("Response") == "True":
            omdb_data = o_res.json()

    if omdb_data:
        title = omdb_data.get("Title") or title
        avg_rating = parse_float(omdb_data.get("imdbRating")) or avg_rating
        description = omdb_data.get("Plot") or description
        duration = parse_int(omdb_data.get("Runtime")) or duration
        release_year = parse_int(omdb_data.get("Year")) or release_year
        if omdb_data.get("Genre") and omdb_data.get("Genre") != "N/A":
            genre = omdb_data.get("Genre")
        if omdb_data.get("Language") and omdb_data.get("Language") != "N/A":
            language = omdb_data.get("Language")
        if not poster_image_url and omdb_data.get("Poster") and omdb_data.get("Poster") != "N/A":
            poster_image_url = omdb_data.get("Poster")
        if not director_name and omdb_data.get("Director") and omdb_data.get("Director") != "N/A":
            director_name = omdb_data.get("Director")
        if not director_nationality and omdb_data.get("Country"):
            director_nationality = omdb_data.get("Country")

    if not title:
        logger.warning(f"No title found for TMDB Movie ID {tmdb_id}. Skipping.")
        return

    # Handle Director
    director_obj = None
    if director_name and director_name != "N/A":
        director_obj = get_or_create_director(db, name=director_name, nationality=director_nationality)

    # Check existing Movie
    movie = db.query(Movie).filter(Movie.tmdb_id == tmdb_id).first()
    if not movie and imdb_id:
        movie = db.query(Movie).filter(Movie.imdb_id == imdb_id).first()

    if movie:
        movie.title = title
        movie.average_rating = avg_rating
        movie.description = description
        movie.duration = duration
        movie.release_year = release_year
        movie.poster_image_url = poster_image_url
        movie.genre = genre
        movie.language = language
        if director_obj:
            movie.director_id = director_obj.id
        logger.info(f"Updated existing Movie record (ID: {movie.id}, Title: '{title}')")
    else:
        movie = Movie(
            id=f"MOV-{tmdb_id}",
            tmdb_id=tmdb_id,
            imdb_id=imdb_id,
            title=title,
            average_rating=avg_rating,
            description=description,
            duration=duration,
            release_year=release_year,
            poster_image_url=poster_image_url,
            genre=genre,
            language=language,
            director_id=director_obj.id if director_obj else None
        )
        db.add(movie)
        logger.info(f"Inserted new Movie record (ID: MOV-{tmdb_id}): '{title}' (TMDB ID: {tmdb_id}, IMDB ID: {imdb_id})")

    db.commit()


def process_movie_list(tmdb_ids: list[int]):
    logger.info(f"Initializing Database Tables...")
    init_db()

    db = SessionLocal()
    try:
        with httpx.Client(timeout=10.0) as client:
            for tmdb_id in tmdb_ids:
                try:
                    process_movie_id(client, db, tmdb_id)
                except Exception as e:
                    logger.error(f"Error processing TMDB Movie ID {tmdb_id}: {e}")
                    db.rollback()
    finally:
        db.close()
    logger.info("Script execution completed successfully.")


def run(start_id: int = 550, end_id: int = 560):
    process_movie_list(list(range(start_id, end_id + 1)))


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        print("Usage:")
        print("  python fetch_movies.py <tmdb_id>             (fetch single movie)")
        print("  python fetch_movies.py <start_id> <end_id>   (fetch range of movies)")
        print("  python fetch_movies.py -l / --list           (interactive list prompt mode)")
        print("\nDefaulting to fetching movie ID 550...")
        process_movie_list([550])

    elif args[0] in ("-l", "--list"):
        print("\n=== Interactive Movie ID List Mode ===")
        print("Enter TMDB Movie IDs one by one (or space/comma separated).")
        print("Type 'q' when finished to start fetching.\n")

        movie_ids = []
        while True:
            try:
                user_input = input("Enter TMDB Movie ID(s) (or 'q' to start): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting interactive prompt.")
                break

            if user_input.lower() in ("q", "quit", "exit"):
                break

            numbers = re.findall(r"\d+", user_input)
            if not numbers:
                print("  Invalid input. Please enter a valid number or 'q' to quit.")
                continue

            for num_str in numbers:
                num = int(num_str)
                if num not in movie_ids:
                    movie_ids.append(num)
                    print(f"  Added Movie ID: {num}")

        if movie_ids:
            print(f"\nStarting fetch process for {len(movie_ids)} movies: {movie_ids}")
            process_movie_list(movie_ids)
        else:
            print("No movie IDs provided. Exiting.")

    elif len(args) == 1:
        try:
            single_id = int(args[0])
            print(f"Fetching single TMDB Movie ID: {single_id}")
            process_movie_list([single_id])
        except ValueError:
            print(f"Invalid movie ID: '{args[0]}'. Must be an integer.")

    elif len(args) >= 2:
        try:
            start_id = int(args[0])
            end_id = int(args[1])
            if start_id > end_id:
                start_id, end_id = end_id, start_id
            movie_ids = list(range(start_id, end_id + 1))
            print(f"Fetching TMDB Movie IDs in range {start_id} to {end_id} ({len(movie_ids)} movies)...")
            process_movie_list(movie_ids)
        except ValueError:
            print(f"Invalid range arguments: {args[0]}, {args[1]}. Must be integers.")

