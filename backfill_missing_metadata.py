import re
import sys
import time
import logging
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Dict, Any, List

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal, init_db
from app.db.models import Movie, Director, WebSeries

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill")

TMDB_BASE_URL = "https://api.themoviedb.org/3"


def parse_int(val: Any) -> Optional[int]:
    if not val:
        return None
    try:
        match = re.search(r"\d+", str(val))
        return int(match.group()) if match else None
    except Exception:
        return None


def fetch_movie_tmdb_details(client: httpx.Client, tmdb_id: int) -> Optional[Dict[str, Any]]:
    url = f"{TMDB_BASE_URL}/movie/{tmdb_id}"
    params = {"api_key": settings.TMDB_API_KEY, "append_to_response": "credits"}
    try:
        res = client.get(url, params=params, timeout=10.0)
        if res.status_code == 200:
            return res.json()
        elif res.status_code == 404:
            return None
        elif res.status_code == 429:
            time.sleep(1.0)
            res = client.get(url, params=params, timeout=10.0)
            if res.status_code == 200:
                return res.json()
    except Exception as ex:
        logger.warning(f"Error fetching TMDB {tmdb_id}: {ex}")
    return None


def fetch_series_tmdb_details(client: httpx.Client, tmdb_id: int) -> Optional[Dict[str, Any]]:
    url = f"{TMDB_BASE_URL}/tv/{tmdb_id}"
    params = {"api_key": settings.TMDB_API_KEY, "append_to_response": "credits,external_ids"}
    try:
        res = client.get(url, params=params, timeout=10.0)
        if res.status_code == 200:
            return res.json()
        elif res.status_code == 429:
            time.sleep(1.0)
            res = client.get(url, params=params, timeout=10.0)
            if res.status_code == 200:
                return res.json()
    except Exception as ex:
        logger.warning(f"Error fetching TMDB TV {tmdb_id}: {ex}")
    return None


def get_or_create_director(db: Session, name: str) -> Director:
    name_clean = name.strip()
    director = db.query(Director).filter(Director.name.ilike(name_clean)).first()
    if not director:
        director = Director(name=name_clean)
        db.add(director)
        db.commit()
        db.refresh(director)
    return director


def backfill_movies(limit: Optional[int] = None, popular_first: bool = True, max_workers: int = 10):
    db: Session = SessionLocal()
    try:
        query = db.query(Movie).filter(
            (Movie.duration.is_(None)) |
            (Movie.director_id.is_(None)) |
            (Movie.imdb_id.is_(None))
        ).filter(Movie.tmdb_id.isnot(None))

        if popular_first:
            query = query.order_by(Movie.average_rating.desc().nullslast(), Movie.release_year.desc().nullslast())

        if limit:
            query = query.limit(limit)

        target_movies = query.all()
        total_targets = len(target_movies)
        logger.info(f"Found {total_targets} movies needing metadata enrichment (duration, imdb_id, director_id)")

        if not target_movies:
            logger.info("No movies need enrichment!")
            return

        movie_items = [(m.id, m.tmdb_id, m.title) for m in target_movies]
    finally:
        db.close()

    success_count = 0
    t0 = time.time()

    def process_one(item):
        m_id, tmdb_id, title = item
        with httpx.Client() as client:
            data = fetch_movie_tmdb_details(client, tmdb_id)
        return m_id, tmdb_id, title, data

    logger.info(f"Starting concurrent fetching with {max_workers} worker threads...")

    batch_updates = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_one, item): item for item in movie_items}

        for idx, fut in enumerate(as_completed(futures), start=1):
            m_id, tmdb_id, title, data = fut.result()
            if not data:
                continue

            imdb_id = data.get("imdb_id")
            runtime = parse_int(data.get("runtime"))
            crew = data.get("credits", {}).get("crew", [])
            dirs = [c["name"] for c in crew if c.get("job") == "Director"]
            director_name = dirs[0] if dirs else None

            batch_updates.append({
                "id": m_id,
                "imdb_id": imdb_id,
                "duration": runtime,
                "director_name": director_name
            })

            if len(batch_updates) >= 50 or idx == total_targets:
                # Bulk commit batch in single round-trip
                save_db = SessionLocal()
                try:
                    target_ids = [up["id"] for up in batch_updates]
                    movies_by_id = {m.id: m for m in save_db.query(Movie).filter(Movie.id.in_(target_ids)).all()}
                    
                    dir_names = {up["director_name"].strip() for up in batch_updates if up.get("director_name")}
                    existing_dirs = {d.name.lower(): d for d in save_db.query(Director).filter(Director.name.in_(dir_names)).all()} if dir_names else {}
                    for name in dir_names:
                        if name.lower() not in existing_dirs:
                            new_d = Director(name=name)
                            save_db.add(new_d)
                            existing_dirs[name.lower()] = new_d
                    save_db.flush()

                    for up in batch_updates:
                        m = movies_by_id.get(up["id"])
                        if not m:
                            continue
                        if up["imdb_id"] and not m.imdb_id:
                            m.imdb_id = up["imdb_id"]
                        if up["duration"] and not m.duration:
                            m.duration = up["duration"]
                        if up.get("director_name") and not m.director_id:
                            dir_obj = existing_dirs.get(up["director_name"].strip().lower())
                            if dir_obj:
                                m.director_id = dir_obj.id
                        success_count += 1
                    save_db.commit()
                except Exception as ex:
                    save_db.rollback()
                    logger.error(f"Error saving batch: {ex}")
                finally:
                    save_db.close()

                elapsed = time.time() - t0
                speed = idx / elapsed if elapsed > 0 else 0
                logger.info(f"Progress: [{idx}/{total_targets}] ({idx/total_targets*100:.1f}%) | Success: {success_count} | Speed: {speed:.1f} movies/sec")
                batch_updates.clear()

    total_time = time.time() - t0
    logger.info(f"======================================================")
    logger.info(f"Finished movie backfill: {success_count}/{total_targets} updated in {total_time:.2f}s ({success_count/total_time:.1f} movies/sec)")
    logger.info(f"======================================================")


def backfill_series(limit: Optional[int] = None, popular_first: bool = True, max_workers: int = 10):
    db: Session = SessionLocal()
    try:
        query = db.query(WebSeries).filter(
            (WebSeries.director_id.is_(None)) |
            (WebSeries.imdb_id.is_(None))
        )

        if popular_first:
            query = query.order_by(WebSeries.average_rating.desc().nullslast())

        if limit:
            query = query.limit(limit)

        target_series = query.all()
        total_targets = len(target_series)
        logger.info(f"Found {total_targets} web series needing metadata enrichment (imdb_id, director_id)")

        if not target_series:
            logger.info("No web series need enrichment!")
            return

        series_items = [(s.series_id, parse_int(s.series_id), s.title) for s in target_series]
    finally:
        db.close()

    success_count = 0
    t0 = time.time()

    def process_one(item):
        s_id, tmdb_id, title = item
        if not tmdb_id:
            return s_id, None, title, None
        with httpx.Client() as client:
            data = fetch_series_tmdb_details(client, tmdb_id)
        return s_id, tmdb_id, title, data

    logger.info(f"Starting concurrent fetching with {max_workers} worker threads...")

    batch_updates = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_one, item): item for item in series_items}

        for idx, fut in enumerate(as_completed(futures), start=1):
            s_id, tmdb_id, title, data = fut.result()
            if not data:
                continue

            ext_ids = data.get("external_ids", {})
            imdb_id = ext_ids.get("imdb_id")
            created_by = data.get("created_by", [])
            creator_name = created_by[0].get("name") if created_by else None
            if not creator_name:
                crew = data.get("credits", {}).get("crew", [])
                dirs = [c["name"] for c in crew if c.get("job") in ("Director", "Executive Producer", "Series Director")]
                creator_name = dirs[0] if dirs else None

            batch_updates.append({
                "series_id": s_id,
                "imdb_id": imdb_id,
                "creator_name": creator_name
            })

            if len(batch_updates) >= 50 or idx == total_targets:
                save_db = SessionLocal()
                try:
                    target_s_ids = [up["series_id"] for up in batch_updates]
                    series_by_id = {s.series_id: s for s in save_db.query(WebSeries).filter(WebSeries.series_id.in_(target_s_ids)).all()}

                    dir_names = {up["creator_name"].strip() for up in batch_updates if up.get("creator_name")}
                    existing_dirs = {d.name.lower(): d for d in save_db.query(Director).filter(Director.name.in_(dir_names)).all()} if dir_names else {}
                    for name in dir_names:
                        if name.lower() not in existing_dirs:
                            new_d = Director(name=name)
                            save_db.add(new_d)
                            existing_dirs[name.lower()] = new_d
                    save_db.flush()

                    for up in batch_updates:
                        s = series_by_id.get(up["series_id"])
                        if not s:
                            continue
                        if up["imdb_id"] and not s.imdb_id:
                            s.imdb_id = up["imdb_id"]
                        if up.get("creator_name") and not s.director_id:
                            dir_obj = existing_dirs.get(up["creator_name"].strip().lower())
                            if dir_obj:
                                s.director_id = dir_obj.id
                        success_count += 1
                    save_db.commit()
                except Exception as ex:
                    save_db.rollback()
                    logger.error(f"Error saving series batch: {ex}")
                finally:
                    save_db.close()

                elapsed = time.time() - t0
                speed = idx / elapsed if elapsed > 0 else 0
                logger.info(f"Progress: [{idx}/{total_targets}] ({idx/total_targets*100:.1f}%) | Success: {success_count} | Speed: {speed:.1f} series/sec")
                batch_updates.clear()

    total_time = time.time() - t0
    logger.info(f"======================================================")
    logger.info(f"Finished series backfill: {success_count}/{total_targets} updated in {total_time:.2f}s")
    logger.info(f"======================================================")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill missing IMDB ID, Duration, and Director ID from TMDB API")
    parser.add_argument("--type", choices=["movies", "series", "all"], default="movies", help="Content type to backfill")
    parser.add_argument("--limit", type=int, default=300, help="Number of records to backfill (default: 200, use 0 for unlimited)")
    parser.add_argument("--popular", action="store_true", default=True, help="Sort by popularity/rating first (default: True)")
    parser.add_argument("--workers", type=int, default=12, help="Number of concurrent worker threads (default: 12)")

    args = parser.parse_args()
    lim = None if args.limit <= 0 else args.limit

    if args.type in ("movies", "all"):
        backfill_movies(limit=lim, popular_first=args.popular, max_workers=args.workers)
    if args.type in ("series", "all"):
        backfill_series(limit=lim, popular_first=args.popular, max_workers=args.workers)
