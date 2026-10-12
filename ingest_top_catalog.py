import asyncio
import logging
import re
import sys
import time
from typing import Dict, List, Optional, Set, Any
import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal, init_db
from app.db.models import Movie, WebSeries, Season, Episode, Director
from app.services.genre_service import analyze_story_lexical as compute_story_genre_percentages

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("catalog_ingestion")

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
TMDB_LOGO_BASE = "https://image.tmdb.org/t/p/w200"

# Canonical TMDB Genre Mappings
TMDB_MOVIE_GENRES = {
    28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy", 80: "Crime",
    99: "Documentary", 18: "Drama", 10751: "Family", 14: "Fantasy", 36: "History",
    27: "Horror", 10402: "Music", 9648: "Mystery", 10749: "Romance", 878: "Sci-Fi",
    10770: "TV Movie", 53: "Thriller", 10752: "War", 37: "Western"
}

TMDB_TV_GENRES = {
    10759: "Action & Adventure", 16: "Animation", 35: "Comedy", 80: "Crime",
    99: "Documentary", 18: "Drama", 10751: "Family", 10762: "Kids",
    9648: "Mystery", 10763: "News", 10764: "Reality", 10765: "Sci-Fi & Fantasy",
    10766: "Soap", 10767: "Talk", 10768: "War & Politics", 37: "Western"
}

def parse_year(date_str: Optional[str]) -> Optional[int]:
    if not date_str:
        return None
    try:
        m = re.search(r"^\d{4}", date_str.strip())
        return int(m.group(0)) if m else None
    except Exception:
        return None

def extract_ott_providers(country_data: Dict[str, Any], tmdb_id: int, is_tv: bool = False) -> Dict[str, Any]:
    watch_link = country_data.get("link") or f"https://www.themoviedb.org/{'tv' if is_tv else 'movie'}/{tmdb_id}/watch"
    structured_providers = []
    seen_names = set()

    for item in country_data.get("flatrate", []):
        name = item.get("provider_name")
        if name and name not in seen_names:
            seen_names.add(name)
            logo = item.get("logo_path")
            structured_providers.append({
                "name": name,
                "type": "Stream (Included)",
                "logo_url": f"{TMDB_LOGO_BASE}{logo}" if logo else None,
                "watch_url": watch_link
            })

    for item in country_data.get("buy", []):
        name = item.get("provider_name")
        if name and name not in seen_names:
            seen_names.add(name)
            logo = item.get("logo_path")
            structured_providers.append({
                "name": name,
                "type": "Buy",
                "logo_url": f"{TMDB_LOGO_BASE}{logo}" if logo else None,
                "watch_url": watch_link
            })

    for item in country_data.get("rent", []):
        name = item.get("provider_name")
        if name and name not in seen_names:
            seen_names.add(name)
            logo = item.get("logo_path")
            structured_providers.append({
                "name": name,
                "type": "Rent",
                "logo_url": f"{TMDB_LOGO_BASE}{logo}" if logo else None,
                "watch_url": watch_link
            })

    # If no providers found from country, provide default streaming portal link
    if not structured_providers:
        structured_providers = [
            {
                "name": "TMDB Watch Portal",
                "type": "Stream / Rent / Buy",
                "logo_url": "https://image.tmdb.org/t/p/w200/9A1JSVmSxsyaBK4SUFsYVqbAYfW.jpg",
                "watch_url": watch_link
            }
        ]

    top_platform = structured_providers[0]["name"] if structured_providers else None
    return {
        "ott_providers": structured_providers,
        "ott_platform": top_platform,
        "ott_url": watch_link
    }

def get_or_create_director(db: Session, name: Optional[str], nationality: Optional[str] = None) -> Optional[int]:
    if not name or name == "N/A":
        return None
    d = db.query(Director).filter(Director.name == name).first()
    if not d:
        d = Director(name=name, nationality=nationality)
        db.add(d)
        db.flush()
    return d.id


async def fetch_top_tv_series(target_count: int = 150):
    logger.info(f"=== Starting Top {target_count} Series Ingestion ===")
    db = SessionLocal()
    existing_tmdb_ids = set(s[0] for s in db.query(WebSeries.tmdb_id).all() if s[0])
    logger.info(f"Existing Series in DB: {len(existing_tmdb_ids)}")

    client = httpx.AsyncClient(timeout=15.0)
    series_candidates: List[Dict[str, Any]] = []
    seen_ids: Set[int] = set(existing_tmdb_ids)

    # 1. Collect series IDs from top_rated and most popular
    try:
        # Collect from top_rated
        for page in range(1, 12):
            url = f"{TMDB_BASE_URL}/tv/top_rated"
            res = await client.get(url, params={"api_key": settings.TMDB_API_KEY, "page": page})
            if res.status_code == 200:
                results = res.json().get("results", [])
                for r in results:
                    sid = r.get("id")
                    if sid and sid not in seen_ids:
                        seen_ids.add(sid)
                        series_candidates.append(r)
            if len(series_candidates) >= target_count + 50:
                break

        # Also collect from discover by vote_count to ensure household classics
        if len(series_candidates) < target_count:
            for page in range(1, 10):
                url = f"{TMDB_BASE_URL}/discover/tv"
                res = await client.get(url, params={
                    "api_key": settings.TMDB_API_KEY,
                    "sort_by": "vote_count.desc",
                    "page": page
                })
                if res.status_code == 200:
                    results = res.json().get("results", [])
                    for r in results:
                        sid = r.get("id")
                        if sid and sid not in seen_ids:
                            seen_ids.add(sid)
                            series_candidates.append(r)
                if len(series_candidates) >= target_count:
                    break

        logger.info(f"Collected {len(series_candidates)} candidate series to process")

        # Process each series in details
        added_count = 0
        for cand in series_candidates[:target_count]:
            tmdb_id = cand["id"]
            # Fetch detailed TV data with credits & watch providers
            detail_url = f"{TMDB_BASE_URL}/tv/{tmdb_id}"
            d_res = await client.get(detail_url, params={
                "api_key": settings.TMDB_API_KEY,
                "append_to_response": "credits,watch/providers,external_ids"
            })
            if d_res.status_code != 200:
                continue

            data = d_res.json()
            title = data.get("name") or data.get("original_name")
            if not title:
                continue

            desc = data.get("overview") or ""
            rating = round(float(data.get("vote_average", 7.5)), 1)
            year = parse_year(data.get("first_air_date"))
            seasons_count = max(1, data.get("number_of_seasons", 1))
            poster_path = data.get("poster_path")
            poster_url = f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else None

            # Genres
            genre_list = [g.get("name") for g in data.get("genres", []) if g.get("name")]
            genre_str = ", ".join(genre_list) if genre_list else "Drama"
            genre_pcts = compute_story_genre_percentages(title, desc, genre_str)

            # Director / Creator
            creator_name = None
            created_by = data.get("created_by", [])
            if created_by:
                creator_name = created_by[0].get("name")
            else:
                crew = data.get("credits", {}).get("crew", [])
                for member in crew:
                    if member.get("job") in ("Director", "Executive Producer", "Showrunner"):
                        creator_name = member.get("name")
                        break

            dir_id = get_or_create_director(db, creator_name)

            # OTT Providers
            watch_data = data.get("watch/providers", {}).get("results", {})
            country_dict = watch_data.get("IN") or watch_data.get("US") or (list(watch_data.values())[0] if watch_data else {})
            ott_info = extract_ott_providers(country_dict, tmdb_id, is_tv=True)

            imdb_id = data.get("external_ids", {}).get("imdb_id")

            series_obj = WebSeries(
                series_id=f"SER-{tmdb_id}",
                tmdb_id=tmdb_id,
                imdb_id=imdb_id,
                title=title,
                average_rating=rating,
                description=desc,
                release_year=year,
                total_seasons=seasons_count,
                poster_image_url=poster_url,
                genre=genre_str,
                genre_percentages=genre_pcts,
                language=data.get("original_language"),
                director_id=dir_id,
                ott_platform=ott_info["ott_platform"],
                ott_url=ott_info["ott_url"],
                ott_providers=ott_info["ott_providers"]
            )
            db.add(series_obj)
            db.flush()

            # Create Season 1 and sample episodes for complete frontend interactivity
            seasons_data = data.get("seasons", [])
            s1_data = next((s for s in seasons_data if s.get("season_number") == 1), None)
            ep_count = s1_data.get("episode_count") if s1_data else 8
            ep_count = max(3, min(ep_count or 8, 12))

            s1 = Season(
                season_id=f"SEAS-{tmdb_id}-1",
                series_id=f"SER-{tmdb_id}",
                season_number=1,
                release_year=year,
                title="Season 1",
                description=f"Season 1 of {title}",
                poster_image_url=poster_url,
                episode_count=ep_count,
                tmdb_id=s1_data.get("id") if s1_data else None
            )
            db.add(s1)
            db.flush()

            for ep_num in range(1, ep_count + 1):
                ep = Episode(
                    episode_id=f"EP-{tmdb_id}-1-{ep_num}",
                    season_id=s1.season_id,
                    episode_number=ep_num,
                    title=f"Episode {ep_num}",
                    duration=45 + (ep_num % 15),
                    description=f"Episode {ep_num} of {title} Season 1.",
                    still_image_url=poster_url,
                    average_rating=rating,
                    release_date=data.get("first_air_date")
                )
                db.add(ep)

            db.commit()
            added_count += 1
            if added_count % 10 == 0 or added_count == target_count:
                logger.info(f"Ingested {added_count}/{target_count} Series: '{title}' (Rating: {rating})")

        logger.info(f"Successfully finished Series ingestion. Added {added_count} new series.")
    finally:
        await client.aclose()
        db.close()


async def fetch_top_movies(target_count: int = 5000):
    logger.info(f"=== Starting Top {target_count} Movies Ingestion ===")
    db = SessionLocal()
    existing_tmdb_ids = set(m[0] for m in db.query(Movie.tmdb_id).all() if m[0])
    existing_movie_ids = set(m[0] for m in db.query(Movie.id).all())
    logger.info(f"Existing Movies in DB: {len(existing_tmdb_ids)}")

    client = httpx.AsyncClient(timeout=20.0, limits=httpx.Limits(max_connections=30, max_keepalive_connections=15))
    seen_ids: Set[int] = set(existing_tmdb_ids)

    # 250 pages * 20 movies = 5,000 movies
    pages_to_fetch = (target_count // 20) + 10
    logger.info(f"Fetching TMDB Top Rated movies across {pages_to_fetch} pages...")

    movie_records_to_insert: List[Dict[str, Any]] = []

    try:
        # Fetch pages in concurrent batches of 10 pages
        batch_size = 10
        total_pages = pages_to_fetch
        
        for start_page in range(1, total_pages + 1, batch_size):
            page_numbers = list(range(start_page, min(start_page + batch_size, total_pages + 1)))
            tasks = [
                client.get(
                    f"{TMDB_BASE_URL}/movie/top_rated",
                    params={"api_key": settings.TMDB_API_KEY, "page": p}
                )
                for p in page_numbers
            ]
            responses = await asyncio.gather(*tasks, return_exceptions=True)

            for res in responses:
                if isinstance(res, httpx.Response) and res.status_code == 200:
                    results = res.json().get("results", [])
                    for m in results:
                        mid = m.get("id")
                        title = m.get("title") or m.get("original_title")
                        if not mid or not title or mid in seen_ids:
                            continue
                        if f"MOV-{mid}" in existing_movie_ids:
                            continue

                        seen_ids.add(mid)
                        desc = m.get("overview") or ""
                        rating = round(float(m.get("vote_average", 7.0)), 1)
                        year = parse_year(m.get("release_date"))
                        poster_path = m.get("poster_path")
                        poster_url = f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else None

                        # Map Genre IDs
                        genre_names = [TMDB_MOVIE_GENRES.get(gid) for gid in m.get("genre_ids", []) if TMDB_MOVIE_GENRES.get(gid)]
                        genre_str = ", ".join(genre_names) if genre_names else "Drama"
                        genre_pcts = compute_story_genre_percentages(title, desc, genre_str)

                        # OTT structured links
                        watch_url = f"https://www.themoviedb.org/movie/{mid}/watch"
                        ott_providers = [
                            {
                                "name": "TMDB Watch Portal",
                                "type": "Stream / Rent / Buy",
                                "logo_url": "https://image.tmdb.org/t/p/w200/9A1JSVmSxsyaBK4SUFsYVqbAYfW.jpg",
                                "watch_url": watch_url
                            }
                        ]

                        movie_records_to_insert.append({
                            "id": f"MOV-{mid}",
                            "tmdb_id": mid,
                            "imdb_id": None,
                            "title": title,
                            "average_rating": rating,
                            "description": desc,
                            "duration": None,
                            "release_year": year,
                            "poster_image_url": poster_url,
                            "genre": genre_str,
                            "genre_percentages": genre_pcts,
                            "language": m.get("original_language"),
                            "director_id": None,
                            "ott_platform": "TMDB Watch Portal",
                            "ott_url": watch_url,
                            "ott_providers": ott_providers
                        })

            logger.info(f"Progress: fetched pages {page_numbers[0]}-{page_numbers[-1]} | Total collected new movies: {len(movie_records_to_insert)}")
            if len(movie_records_to_insert) >= target_count:
                break

        # Also supplement from discover sorted by vote_count if needed to hit exact target
        if len(movie_records_to_insert) < target_count:
            logger.info("Supplementing with most popular & highest voted titles...")
            for page in range(1, 40):
                res = await client.get(
                    f"{TMDB_BASE_URL}/discover/movie",
                    params={"api_key": settings.TMDB_API_KEY, "sort_by": "vote_count.desc", "page": page}
                )
                if res.status_code == 200:
                    results = res.json().get("results", [])
                    for m in results:
                        mid = m.get("id")
                        title = m.get("title") or m.get("original_title")
                        if not mid or not title or mid in seen_ids:
                            continue
                        if f"MOV-{mid}" in existing_movie_ids:
                            continue

                        seen_ids.add(mid)
                        desc = m.get("overview") or ""
                        rating = round(float(m.get("vote_average", 7.0)), 1)
                        year = parse_year(m.get("release_date"))
                        poster_path = m.get("poster_path")
                        poster_url = f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else None

                        genre_names = [TMDB_MOVIE_GENRES.get(gid) for gid in m.get("genre_ids", []) if TMDB_MOVIE_GENRES.get(gid)]
                        genre_str = ", ".join(genre_names) if genre_names else "Drama"
                        genre_pcts = compute_story_genre_percentages(title, desc, genre_str)

                        watch_url = f"https://www.themoviedb.org/movie/{mid}/watch"
                        ott_providers = [
                            {
                                "name": "TMDB Watch Portal",
                                "type": "Stream / Rent / Buy",
                                "logo_url": "https://image.tmdb.org/t/p/w200/9A1JSVmSxsyaBK4SUFsYVqbAYfW.jpg",
                                "watch_url": watch_url
                            }
                        ]

                        movie_records_to_insert.append({
                            "id": f"MOV-{mid}",
                            "tmdb_id": mid,
                            "imdb_id": None,
                            "title": title,
                            "average_rating": rating,
                            "description": desc,
                            "duration": None,
                            "release_year": year,
                            "poster_image_url": poster_url,
                            "genre": genre_str,
                            "genre_percentages": genre_pcts,
                            "language": m.get("original_language"),
                            "director_id": None,
                            "ott_platform": "TMDB Watch Portal",
                            "ott_url": watch_url,
                            "ott_providers": ott_providers
                        })

                if len(movie_records_to_insert) >= target_count:
                    break

        # Trim to exact target
        final_records = movie_records_to_insert[:target_count]
        logger.info(f"Beginning Database Insertion for {len(final_records)} Movies in batches of 250...")

        chunk_size = 250
        inserted_so_far = 0
        for i in range(0, len(final_records), chunk_size):
            chunk = final_records[i:i + chunk_size]
            movies = [Movie(**item) for item in chunk]
            db.bulk_save_objects(movies)
            db.commit()
            inserted_so_far += len(chunk)
            logger.info(f"Committed batch: {inserted_so_far}/{len(final_records)} movies inserted into PostgreSQL.")

        logger.info(f"=== Successfully completed Movies Ingestion! Total newly added: {inserted_so_far} ===")
    finally:
        await client.aclose()
        db.close()


async def main():
    logger.info("Initializing DB Schema...")
    init_db()

    # 1. Fetch Top 150 Series first (rich details, seasons & episodes)
    await fetch_top_tv_series(target_count=150)

    # 2. Fetch Top 5000 Movies
    await fetch_top_movies(target_count=5000)


if __name__ == "__main__":
    asyncio.run(main())
