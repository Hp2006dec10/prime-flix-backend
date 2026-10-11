import argparse
import logging
import re
import sys
from typing import Any, Dict, List, Optional
import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Base, Director, Episode, Season, WebSeries
from app.db.session import SessionLocal, engine, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

TMDB_BASE_URL = "https://api.themoviedb.org/3"
OMDB_BASE_URL = "http://www.omdbapi.com"

# Curated list of top acclaimed TV Shows & Web Series with TMDB IDs
DEFAULT_SERIES_TMDB_IDS = [
    1396,   # Breaking Bad
    1399,   # Game of Thrones
    66732,  # Stranger Things
    87108,  # Chernobyl
    76479,  # The Boys
    60059,  # Better Call Saul
    84958,  # Loki
    70523,  # Dark
    76331,  # Succession
    94605,  # Arcane
    60574,  # Peaky Blinders
    95557,  # Severance
    100088, # The Last of Us
    19885,  # Sherlock
    63351,  # Narcos
    60622,  # Fargo
    46648,  # True Detective
    42009,  # Black Mirror
    94997,  # House of the Dragon
    67744,  # Mindhunter
]


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


def get_or_create_director(db: Session, name: str, nationality: Optional[str] = None) -> Director:
    director = db.query(Director).filter(Director.name == name).first()
    if not director:
        director = Director(name=name, nationality=nationality)
        db.add(director)
        db.commit()
        db.refresh(director)
        logger.info(f"Created new Director/Creator: '{name}' (Nationality: {nationality})")
    else:
        if not director.nationality and nationality:
            director.nationality = nationality
            db.commit()
            db.refresh(director)
    return director


def fetch_tmdb_tv_show(client: httpx.Client, tmdb_id: int) -> Optional[Dict[str, Any]]:
    url = f"{TMDB_BASE_URL}/tv/{tmdb_id}"
    params = {"api_key": settings.TMDB_API_KEY}
    res = client.get(url, params=params)
    if res.status_code == 200:
        return res.json()
    elif res.status_code == 404:
        logger.warning(f"TMDB TV Show ID {tmdb_id} not found (404). Skipping.")
    else:
        logger.error(f"TMDB TV Show ID {tmdb_id} returned status code {res.status_code}: {res.text}")
    return None


def fetch_tmdb_external_ids(client: httpx.Client, tmdb_id: int) -> Dict[str, Optional[str]]:
    url = f"{TMDB_BASE_URL}/tv/{tmdb_id}/external_ids"
    params = {"api_key": settings.TMDB_API_KEY}
    res = client.get(url, params=params)
    if res.status_code == 200:
        data = res.json()
        return {"imdb_id": data.get("imdb_id")}
    return {"imdb_id": None}


def fetch_tmdb_tv_director(client: httpx.Client, tmdb_id: int, show_data: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """
    Finds the show creator or directing lead.
    First checks show's 'created_by' array, then credits.
    """
    params = {"api_key": settings.TMDB_API_KEY}

    # 1. Check created_by
    created_by = show_data.get("created_by", [])
    if created_by:
        creator = created_by[0]
        creator_name = creator.get("name")
        person_id = creator.get("id")
        nationality = None
        if person_id:
            p_res = client.get(f"{TMDB_BASE_URL}/person/{person_id}", params=params)
            if p_res.status_code == 200:
                nationality = p_res.json().get("place_of_birth")
        return {"name": creator_name, "nationality": nationality}

    # 2. Check credits for Director or Executive Producer
    cred_res = client.get(f"{TMDB_BASE_URL}/tv/{tmdb_id}/credits", params=params)
    if cred_res.status_code == 200:
        crew = cred_res.json().get("crew", [])
        for person in crew:
            job = person.get("job")
            if job in ("Director", "Executive Producer", "Series Director"):
                person_name = person.get("name")
                person_id = person.get("id")
                nationality = None
                if person_id:
                    p_res = client.get(f"{TMDB_BASE_URL}/person/{person_id}", params=params)
                    if p_res.status_code == 200:
                        nationality = p_res.json().get("place_of_birth")
                return {"name": person_name, "nationality": nationality}

    return {"name": None, "nationality": None}


def fetch_tmdb_season_details(client: httpx.Client, tmdb_id: int, season_number: int) -> Optional[Dict[str, Any]]:
    url = f"{TMDB_BASE_URL}/tv/{tmdb_id}/season/{season_number}"
    params = {"api_key": settings.TMDB_API_KEY}
    res = client.get(url, params=params)
    if res.status_code == 200:
        return res.json()
    else:
        logger.warning(f"Could not fetch Season {season_number} for TV Show {tmdb_id} (status: {res.status_code})")
        return None


def process_tv_show(client: httpx.Client, db: Session, tmdb_id: int):
    logger.info(f"==================================================")
    logger.info(f"Processing TMDB TV Show ID: {tmdb_id}...")

    show_data = fetch_tmdb_tv_show(client, tmdb_id)
    if not show_data:
        return

    ext_ids = fetch_tmdb_external_ids(client, tmdb_id)
    imdb_id = ext_ids.get("imdb_id")

    title = show_data.get("name") or show_data.get("original_name")
    if not title:
        logger.warning(f"No title found for TMDB TV Show {tmdb_id}. Skipping.")
        return

    avg_rating = parse_float(show_data.get("vote_average"))
    description = show_data.get("overview")
    first_air_date = show_data.get("first_air_date")
    release_year = parse_int(first_air_date[:4]) if first_air_date and len(first_air_date) >= 4 else None
    total_seasons = show_data.get("number_of_seasons") or 1

    # Genres
    genre_names = [g.get("name") for g in show_data.get("genres", []) if g.get("name")]
    genre = ", ".join(genre_names) if genre_names else None

    # Language
    langs = [l.get("english_name") or l.get("name") for l in show_data.get("spoken_languages", []) if l.get("name")]
    language = ", ".join(langs) if langs else show_data.get("original_language")

    # Poster URL
    poster_path = show_data.get("poster_path")
    poster_image_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None

    # Director / Creator
    dir_info = fetch_tmdb_tv_director(client, tmdb_id, show_data)
    director_name = dir_info.get("name")
    director_nationality = dir_info.get("nationality")
    director_obj = None
    if director_name:
        director_obj = get_or_create_director(db, name=director_name, nationality=director_nationality)

    series_id = f"SER-{tmdb_id}"

    # Upsert WebSeries
    series = db.query(WebSeries).filter(WebSeries.series_id == series_id).first()
    if not series and imdb_id:
        series = db.query(WebSeries).filter(WebSeries.imdb_id == imdb_id).first()

    if series:
        series.title = title
        series.average_rating = avg_rating
        series.description = description
        series.release_year = release_year
        series.total_seasons = total_seasons
        series.poster_image_url = poster_image_url
        series.genre = genre
        series.language = language
        if director_obj:
            series.director_id = director_obj.id
        logger.info(f"Updated existing WebSeries: '{title}' ({series_id})")
    else:
        series = WebSeries(
            series_id=series_id,
            tmdb_id=tmdb_id,
            imdb_id=imdb_id,
            title=title,
            average_rating=avg_rating,
            description=description,
            release_year=release_year,
            total_seasons=total_seasons,
            poster_image_url=poster_image_url,
            genre=genre,
            language=language,
            director_id=director_obj.id if director_obj else None,
        )
        db.add(series)
        logger.info(f"Created new WebSeries: '{title}' ({series_id}, Release: {release_year}, Seasons: {total_seasons})")

    db.commit()
    db.refresh(series)

    # Process Seasons and Episodes
    raw_seasons = show_data.get("seasons", [])
    # Filter out Season 0 (Specials) if there are normal seasons
    canonical_seasons = [s for s in raw_seasons if s.get("season_number", 0) > 0]
    if not canonical_seasons:
        canonical_seasons = raw_seasons  # fallback if only season 0 exists

    total_episodes_saved = 0

    for s_meta in canonical_seasons:
        season_num = s_meta.get("season_number", 1)
        season_id = f"SEAS-{tmdb_id}-{season_num}"

        logger.info(f"Fetching Season {season_num} for '{title}'...")
        s_detail = fetch_tmdb_season_details(client, tmdb_id, season_num)
        if not s_detail:
            continue

        s_air_date = s_detail.get("air_date") or s_meta.get("air_date")
        s_year = parse_int(s_air_date[:4]) if s_air_date and len(s_air_date) >= 4 else release_year
        s_poster = s_detail.get("poster_path")
        s_poster_url = f"https://image.tmdb.org/t/p/w500{s_poster}" if s_poster else poster_image_url
        episodes_list = s_detail.get("episodes", [])
        ep_count = len(episodes_list)

        # Upsert Season
        season = db.query(Season).filter(Season.season_id == season_id).first()
        if season:
            season.title = s_detail.get("name") or f"Season {season_num}"
            season.description = s_detail.get("overview")
            season.release_year = s_year
            season.poster_image_url = s_poster_url
            season.episode_count = ep_count
            season.tmdb_id = s_detail.get("id")
        else:
            season = Season(
                season_id=season_id,
                series_id=series_id,
                season_number=season_num,
                release_year=s_year,
                title=s_detail.get("name") or f"Season {season_num}",
                description=s_detail.get("overview"),
                poster_image_url=s_poster_url,
                episode_count=ep_count,
                tmdb_id=s_detail.get("id"),
            )
            db.add(season)

        db.commit()
        db.refresh(season)

        # Upsert Episodes
        for ep in episodes_list:
            ep_num = ep.get("episode_number")
            ep_id = f"EP-{tmdb_id}-{season_num}-{ep_num}"
            ep_title = ep.get("name") or f"Episode {ep_num}"
            ep_duration = parse_int(ep.get("runtime"))
            ep_still = ep.get("still_path")
            ep_still_url = f"https://image.tmdb.org/t/p/w500{ep_still}" if ep_still else None
            ep_rating = parse_float(ep.get("vote_average"))
            ep_air_date = ep.get("air_date")
            ep_overview = ep.get("overview")

            episode = db.query(Episode).filter(Episode.episode_id == ep_id).first()
            if episode:
                episode.title = ep_title
                episode.duration = ep_duration
                episode.description = ep_overview
                episode.still_image_url = ep_still_url
                episode.average_rating = ep_rating
                episode.release_date = ep_air_date
                episode.tmdb_id = ep.get("id")
            else:
                episode = Episode(
                    episode_id=ep_id,
                    season_id=season_id,
                    episode_number=ep_num,
                    title=ep_title,
                    duration=ep_duration,
                    description=ep_overview,
                    still_image_url=ep_still_url,
                    average_rating=ep_rating,
                    release_date=ep_air_date,
                    tmdb_id=ep.get("id"),
                )
                db.add(episode)
            total_episodes_saved += 1

        db.commit()

    logger.info(
        f"Successfully processed '{title}': {len(canonical_seasons)} seasons, {total_episodes_saved} episodes saved."
    )


def search_tmdb_tv(client: httpx.Client, query: str) -> Optional[int]:
    url = f"{TMDB_BASE_URL}/search/tv"
    params = {"api_key": settings.TMDB_API_KEY, "query": query}
    res = client.get(url, params=params)
    if res.status_code == 200:
        results = res.json().get("results", [])
        if results:
            top = results[0]
            logger.info(f"Search '{query}' found: '{top.get('name')}' (TMDB ID: {top.get('id')})")
            return top.get("id")
    logger.warning(f"No TV Show found for search query '{query}'.")
    return None


def main():
    parser = argparse.ArgumentParser(description="Fetch and ingest TV Shows, Seasons, and Episodes from TMDB into PrimeFlix database.")
    parser.add_argument("--id", type=int, help="TMDB TV Show ID to fetch")
    parser.add_argument("--search", type=str, help="Search for TV show by name on TMDB and ingest")
    parser.add_argument("--all", action="store_true", help="Fetch all default curated TV shows & web series")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of shows from the default list to fetch")

    args = parser.parse_args()

    init_db()
    db = SessionLocal()

    with httpx.Client(timeout=25.0) as client:
        if args.id:
            process_tv_show(client, db, args.id)
        elif args.search:
            show_id = search_tmdb_tv(client, args.search)
            if show_id:
                process_tv_show(client, db, show_id)
        else:
            # Default or --all
            ids_to_fetch = DEFAULT_SERIES_TMDB_IDS
            if args.limit:
                ids_to_fetch = ids_to_fetch[: args.limit]

            logger.info(f"Starting ingestion of {len(ids_to_fetch)} TV shows & web series...")
            for idx, tmdb_id in enumerate(ids_to_fetch, start=1):
                try:
                    logger.info(f"[{idx}/{len(ids_to_fetch)}] Ingesting TMDB ID {tmdb_id}...")
                    process_tv_show(client, db, tmdb_id)
                except Exception as e:
                    logger.error(f"Error processing TMDB ID {tmdb_id}: {e}", exc_info=True)

    db.close()
    logger.info("TV Show & Web Series ingestion script completed successfully!")


if __name__ == "__main__":
    main()
