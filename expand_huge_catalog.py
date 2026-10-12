import asyncio
import logging
import re
import sys
import time
from typing import Dict, List, Optional, Set, Any, Tuple
import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal, init_db
from app.db.models import Movie, WebSeries, Season, Episode, Director, CinematicUniverse, UniverseContent
from app.services.genre_service import analyze_story_lexical as compute_story_genre_percentages

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("huge_catalog_expansion")

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

# Major Cinematic Universes & TMDB Collections / Search queries
CINEMATIC_UNIVERSES_CONFIG = [
    {
        "name": "Pirates of the Caribbean",
        "description": "The swashbuckling Disney pirate adventures following Captain Jack Sparrow and the supernatural mysteries of the high seas.",
        "collection_id": 295
    },
    {
        "name": "Wizarding World",
        "description": "The magical universe of J.K. Rowling encompassing Harry Potter at Hogwarts and the Fantastic Beasts global chronicles.",
        "collection_id": 1241,
        "extra_collection_ids": [435634] # Fantastic Beasts Collection
    },
    {
        "name": "Star Wars Universe",
        "description": "The sprawling Lucasfilm galaxy far, far away chronicling the Skywalkers, Jedi, Sith, and the galactic rebellion.",
        "collection_id": 10,
        "extra_tmdb_movies": [330459, 348350], # Rogue One, Solo
        "series_tmdb_ids": [82856, 83867, 114461, 4174] # The Mandalorian, Andor, Ahsoka, Clone Wars
    },
    {
        "name": "Middle-earth Universe",
        "description": "J.R.R. Tolkien's high-fantasy world chronicling the quest to destroy the One Ring and the history of Arda.",
        "collection_id": 119, # LOTR
        "extra_collection_ids": [121938], # The Hobbit
        "series_tmdb_ids": [76479] # Rings of Power
    },
    {
        "name": "Fast & Furious Saga",
        "description": "The high-octane vehicular action heist franchise centered around Dominic Toretto and family.",
        "collection_id": 9485
    },
    {
        "name": "Mission: Impossible",
        "description": "The pulse-pounding espionage franchise following Ethan Hunt and the IMF team tackling impossible missions.",
        "collection_id": 87359
    },
    {
        "name": "Jurassic Park & World Universe",
        "description": "Michael Crichton's genetic prehistoric saga where cloned dinosaurs roam Isla Nublar and modern civilization.",
        "collection_id": 328
    },
    {
        "name": "MonsterVerse",
        "description": "The titanic cinematic universe featuring Godzilla, King Kong, Monarch, and ancient Titans.",
        "collection_id": 535313,
        "extra_tmdb_movies": [124905, 345940, 383498, 399566, 823464],
        "series_tmdb_ids": [96462] # Monarch: Legacy of Monsters
    },
    {
        "name": "Transformers Universe",
        "description": "The interstellar conflict between heroic Autobots and evil Decepticons from Cybertron on planet Earth.",
        "collection_id": 8650
    },
    {
        "name": "James Bond 007",
        "description": "The legendary British Secret Intelligence Service MI6 franchise following Agent 007 with a license to kill.",
        "collection_id": 645,
        "extra_tmdb_movies": [36557, 10764, 37724, 206647, 370172] # Modern Craig Bond films
    },
    {
        "name": "Studio Ghibli Universe",
        "description": "The timeless animated masterpieces crafted by Hayao Miyazaki and Isao Takahata exploring human nature and spirit realms.",
        "extra_tmdb_movies": [129, 128, 4935, 12477, 81, 15370, 508883, 378064]
    },
    {
        "name": "Spider-Verse & Sony Marvel Universe",
        "description": "The interconnected multiverse of Spider-heroes, Venom, and Spider-Man lore.",
        "extra_tmdb_movies": [324857, 569094, 335983, 580489, 634492]
    }
]

def parse_year(date_str: Optional[str]) -> Optional[int]:
    if not date_str:
        return None
    try:
        m = re.search(r"^\d{4}", date_str.strip())
        return int(m.group(0)) if m else None
    except Exception:
        return None

def build_default_ott(tmdb_id: int, is_tv: bool = False) -> Tuple[List[Dict[str, Any]], str, str]:
    url = f"https://www.themoviedb.org/{'tv' if is_tv else 'movie'}/{tmdb_id}/watch"
    providers = [
        {
            "name": "TMDB Watch Portal",
            "type": "Stream / Rent / Buy",
            "logo_url": "https://image.tmdb.org/t/p/w200/9A1JSVmSxsyaBK4SUFsYVqbAYfW.jpg",
            "watch_url": url
        }
    ]
    return providers, "TMDB Watch Portal", url


async def populate_cinematic_universes(client: httpx.AsyncClient, db: Session):
    logger.info("=== Phase 1: Expanding Cinematic Universes & Franchise Continuations ===")
    
    # Cache existing movies and series IDs
    existing_movies = {m.tmdb_id: m for m in db.query(Movie).all() if m.tmdb_id}
    existing_series = {s.tmdb_id: s for s in db.query(WebSeries).all() if s.tmdb_id}
    existing_movie_ids = set(m.id for m in db.query(Movie.id).all())

    for u_cfg in CINEMATIC_UNIVERSES_CONFIG:
        u_name = u_cfg["name"]
        u_desc = u_cfg.get("description", "")
        
        # Get or create CinematicUniverse
        universe = db.query(CinematicUniverse).filter(CinematicUniverse.name == u_name).first()
        if not universe:
            universe = CinematicUniverse(name=u_name, description=u_desc)
            db.add(universe)
            db.flush()
            logger.info(f"Created new Cinematic Universe: '{u_name}' (ID: {universe.id})")
        else:
            universe.description = u_desc
            db.flush()

        # Collect candidate movie parts
        movie_items: List[Dict[str, Any]] = []
        collection_ids = [u_cfg["collection_id"]] if "collection_id" in u_cfg else []
        collection_ids.extend(u_cfg.get("extra_collection_ids", []))

        for cid in collection_ids:
            col_url = f"{TMDB_BASE_URL}/collection/{cid}"
            res = await client.get(col_url, params={"api_key": settings.TMDB_API_KEY})
            if res.status_code == 200:
                parts = res.json().get("parts", [])
                movie_items.extend(parts)

        # Extra explicit TMDB movie IDs
        for mid in u_cfg.get("extra_tmdb_movies", []):
            if not any(m.get("id") == mid for m in movie_items):
                m_res = await client.get(f"{TMDB_BASE_URL}/movie/{mid}", params={"api_key": settings.TMDB_API_KEY})
                if m_res.status_code == 200:
                    movie_items.append(m_res.json())

        # Sort movie parts by release date
        movie_items.sort(key=lambda x: x.get("release_date") or "9999")

        # Ingest missing movies and create UniverseContent
        existing_u_cids = set(c.content_id for c in universe.contents)

        order_idx = 1
        for m_data in movie_items:
            mid = m_data.get("id")
            if not mid:
                continue

            content_id = f"MOV-{mid}"
            
            # Check if movie exists in DB, else insert
            if mid not in existing_movies and content_id not in existing_movie_ids:
                title = m_data.get("title") or m_data.get("original_title") or f"Movie {mid}"
                desc = m_data.get("overview") or ""
                rating = round(float(m_data.get("vote_average", 7.0)), 1)
                year = parse_year(m_data.get("release_date"))
                poster_path = m_data.get("poster_path")
                poster_url = f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else None
                genre_names = [TMDB_MOVIE_GENRES.get(gid) for gid in m_data.get("genre_ids", []) if TMDB_MOVIE_GENRES.get(gid)]
                genre_str = ", ".join(genre_names) if genre_names else "Action, Adventure"
                genre_pcts = compute_story_genre_percentages(title, desc, genre_str)
                ott_prov, ott_plat, ott_url = build_default_ott(mid, is_tv=False)

                new_movie = Movie(
                    id=content_id,
                    tmdb_id=mid,
                    title=title,
                    average_rating=rating,
                    description=desc,
                    release_year=year,
                    poster_image_url=poster_url,
                    genre=genre_str,
                    genre_percentages=genre_pcts,
                    language=m_data.get("original_language"),
                    ott_platform=ott_plat,
                    ott_url=ott_url,
                    ott_providers=ott_prov
                )
                db.add(new_movie)
                db.flush()
                existing_movies[mid] = new_movie
                existing_movie_ids.add(content_id)

            # Link in UniverseContent
            if content_id not in existing_u_cids:
                uc = UniverseContent(
                    universe_id=universe.id,
                    content_id=content_id,
                    content_type="movie",
                    order_in_universe=order_idx
                )
                db.add(uc)
                existing_u_cids.add(content_id)
            order_idx += 1

        # Link series if configured
        for stid in u_cfg.get("series_tmdb_ids", []):
            s_content_id = f"SER-{stid}"
            if stid not in existing_series:
                # Fetch series
                s_res = await client.get(f"{TMDB_BASE_URL}/tv/{stid}", params={"api_key": settings.TMDB_API_KEY})
                if s_res.status_code == 200:
                    s_data = s_res.json()
                    stitle = s_data.get("name") or s_data.get("original_name")
                    sdesc = s_data.get("overview") or ""
                    srating = round(float(s_data.get("vote_average", 8.0)), 1)
                    syear = parse_year(s_data.get("first_air_date"))
                    sposter = f"{TMDB_IMAGE_BASE}{s_data.get('poster_path')}" if s_data.get('poster_path') else None
                    s_genres = ", ".join([g.get("name") for g in s_data.get("genres", []) if g.get("name")]) or "Sci-Fi & Fantasy"
                    s_pcts = compute_story_genre_percentages(stitle, sdesc, s_genres)
                    ott_prov, ott_plat, ott_url = build_default_ott(stid, is_tv=True)

                    new_s = WebSeries(
                        series_id=s_content_id,
                        tmdb_id=stid,
                        title=stitle,
                        average_rating=srating,
                        description=sdesc,
                        release_year=syear,
                        total_seasons=s_data.get("number_of_seasons", 1),
                        poster_image_url=sposter,
                        genre=s_genres,
                        genre_percentages=s_pcts,
                        language=s_data.get("original_language"),
                        ott_platform=ott_plat,
                        ott_url=ott_url,
                        ott_providers=ott_prov
                    )
                    db.add(new_s)
                    db.flush()
                    existing_series[stid] = new_s

            if s_content_id not in existing_u_cids:
                uc = UniverseContent(
                    universe_id=universe.id,
                    content_id=s_content_id,
                    content_type="web_series",
                    order_in_universe=order_idx
                )
                db.add(uc)
                existing_u_cids.add(s_content_id)
                order_idx += 1

        db.commit()
        logger.info(f"Universe '{u_name}' updated with {len(existing_u_cids)} titles.")

    logger.info("Cinematic Universes successfully populated!")


async def ingest_web_series_batch(
    client: httpx.AsyncClient,
    db: Session,
    target_count: int = 5000,
    animation_ratio: float = 0.40
):
    logger.info(f"=== Phase 2: Ingesting {target_count} More Web Series ({int(animation_ratio*100)}% Animation / {int((1-animation_ratio)*100)}% Non-Animation) ===")
    
    existing_tmdb_ids = set(s[0] for s in db.query(WebSeries.tmdb_id).all() if s[0])
    existing_series_ids = set(s[0] for s in db.query(WebSeries.series_id).all())
    logger.info(f"Existing Series in DB: {len(existing_series_ids)}")

    anim_target = int(target_count * animation_ratio)
    live_target = target_count - anim_target

    series_records: List[Dict[str, Any]] = []
    seen_ids: Set[int] = set(existing_tmdb_ids)

    # 1. Fetch Animation Series (genre 16)
    logger.info(f"Fetching ~{anim_target} Animation & Anime series...")
    anim_pages = (anim_target // 20) + 15
    for page in range(1, anim_pages + 1):
        try:
            res = await client.get(
                f"{TMDB_BASE_URL}/discover/tv",
                params={
                    "api_key": settings.TMDB_API_KEY,
                    "with_genres": "16",
                    "sort_by": "popularity.desc",
                    "page": page
                }
            )
            if res.status_code == 200:
                for item in res.json().get("results", []):
                    sid = item.get("id")
                    title = item.get("name") or item.get("original_name")
                    if not sid or not title or sid in seen_ids or f"SER-{sid}" in existing_series_ids:
                        continue
                    seen_ids.add(sid)
                    desc = item.get("overview") or ""
                    rating = round(float(item.get("vote_average", 7.5)), 1)
                    year = parse_year(item.get("first_air_date"))
                    poster = f"{TMDB_IMAGE_BASE}{item.get('poster_path')}" if item.get("poster_path") else None
                    genre_names = [TMDB_TV_GENRES.get(gid, "Animation") for gid in item.get("genre_ids", []) if TMDB_TV_GENRES.get(gid)]
                    if "Animation" not in genre_names:
                        genre_names.insert(0, "Animation")
                    genre_str = ", ".join(genre_names)
                    pcts = compute_story_genre_percentages(title, desc, genre_str)
                    ott_prov, ott_plat, ott_url = build_default_ott(sid, is_tv=True)

                    series_records.append({
                        "series_id": f"SER-{sid}",
                        "tmdb_id": sid,
                        "title": title,
                        "average_rating": rating,
                        "description": desc,
                        "release_year": year,
                        "total_seasons": 1,
                        "poster_image_url": poster,
                        "genre": genre_str,
                        "genre_percentages": pcts,
                        "language": item.get("original_language"),
                        "ott_platform": ott_plat,
                        "ott_url": ott_url,
                        "ott_providers": ott_prov
                    })
            if len(series_records) >= anim_target:
                break
        except Exception as e:
            logger.warning(f"Error fetching TV animation page {page}: {e}")

    logger.info(f"Collected {len(series_records)} Animation Series. Now collecting Live-Action series...")

    # 2. Fetch Live Action Series (without genre 16)
    live_start_idx = len(series_records)
    live_pages = (live_target // 20) + 20
    for page in range(1, live_pages + 1):
        try:
            res = await client.get(
                f"{TMDB_BASE_URL}/discover/tv",
                params={
                    "api_key": settings.TMDB_API_KEY,
                    "without_genres": "16",
                    "sort_by": "popularity.desc",
                    "page": page
                }
            )
            if res.status_code == 200:
                for item in res.json().get("results", []):
                    sid = item.get("id")
                    title = item.get("name") or item.get("original_name")
                    if not sid or not title or sid in seen_ids or f"SER-{sid}" in existing_series_ids:
                        continue
                    seen_ids.add(sid)
                    desc = item.get("overview") or ""
                    rating = round(float(item.get("vote_average", 7.0)), 1)
                    year = parse_year(item.get("first_air_date"))
                    poster = f"{TMDB_IMAGE_BASE}{item.get('poster_path')}" if item.get("poster_path") else None
                    genre_names = [TMDB_TV_GENRES.get(gid, "Drama") for gid in item.get("genre_ids", []) if TMDB_TV_GENRES.get(gid)]
                    genre_str = ", ".join(genre_names) if genre_names else "Drama"
                    pcts = compute_story_genre_percentages(title, desc, genre_str)
                    ott_prov, ott_plat, ott_url = build_default_ott(sid, is_tv=True)

                    series_records.append({
                        "series_id": f"SER-{sid}",
                        "tmdb_id": sid,
                        "title": title,
                        "average_rating": rating,
                        "description": desc,
                        "release_year": year,
                        "total_seasons": 1,
                        "poster_image_url": poster,
                        "genre": genre_str,
                        "genre_percentages": pcts,
                        "language": item.get("original_language"),
                        "ott_platform": ott_plat,
                        "ott_url": ott_url,
                        "ott_providers": ott_prov
                    })
            if len(series_records) >= target_count:
                break
        except Exception as e:
            logger.warning(f"Error fetching TV live-action page {page}: {e}")

    # Trim to exact target
    final_series = series_records[:target_count]
    logger.info(f"Beginning Database Insertion for {len(final_series)} Series in batches of 250...")

    chunk_size = 250
    inserted = 0
    for i in range(0, len(final_series), chunk_size):
        chunk = final_series[i:i + chunk_size]
        
        series_objs = [WebSeries(**item) for item in chunk]
        db.bulk_save_objects(series_objs)
        db.flush()

        # Generate Season 1 and sample episodes
        seasons_objs = []
        episodes_objs = []
        for item in chunk:
            sid = item["series_id"]
            s_num = 1
            season_id = f"SEAS-{item['tmdb_id']}-1"
            seasons_objs.append(
                Season(
                    season_id=season_id,
                    series_id=sid,
                    season_number=1,
                    release_year=item["release_year"],
                    title="Season 1",
                    description=f"Season 1 of {item['title']}",
                    poster_image_url=item["poster_image_url"],
                    episode_count=6
                )
            )
            for ep_num in range(1, 7):
                episodes_objs.append(
                    Episode(
                        episode_id=f"EP-{item['tmdb_id']}-1-{ep_num}",
                        season_id=season_id,
                        episode_number=ep_num,
                        title=f"Episode {ep_num}",
                        duration=45,
                        description=f"Episode {ep_num} of {item['title']} Season 1.",
                        still_image_url=item["poster_image_url"],
                        average_rating=item["average_rating"]
                    )
                )

        db.bulk_save_objects(seasons_objs)
        db.bulk_save_objects(episodes_objs)
        db.commit()
        inserted += len(chunk)
        logger.info(f"Committed Series batch: {inserted}/{len(final_series)} added to DB.")

    logger.info(f"=== Successfully finished Series Expansion! Added {inserted} series ===")


async def ingest_movies_batch(
    client: httpx.AsyncClient,
    db: Session,
    target_count: int = 50000,
    animation_ratio: float = 0.40
):
    logger.info(f"=== Phase 3: Ingesting {target_count} More Movies ({int(animation_ratio*100)}% Animation / {int((1-animation_ratio)*100)}% Non-Animation) ===")
    
    existing_tmdb_ids = set(m[0] for m in db.query(Movie.tmdb_id).all() if m[0])
    existing_movie_ids = set(m[0] for m in db.query(Movie.id).all())
    logger.info(f"Existing Movies in DB: {len(existing_movie_ids)}")

    anim_target = int(target_count * animation_ratio)
    live_target = target_count - anim_target

    movie_records: List[Dict[str, Any]] = []
    seen_ids: Set[int] = set(existing_tmdb_ids)

    # 1. Fetch Animation Movies (genre 16) across years
    logger.info(f"Fetching ~{anim_target} Animation Movies...")
    year_blocks = [
        (2020, 2026), (2015, 2019), (2010, 2014), (2000, 2009), (1990, 1999), (1970, 1989)
    ]
    
    for y_start, y_end in year_blocks:
        if len(movie_records) >= anim_target:
            break
        logger.info(f"Animation fetch block: {y_start} to {y_end}...")
        for page in range(1, 100):
            try:
                res = await client.get(
                    f"{TMDB_BASE_URL}/discover/movie",
                    params={
                        "api_key": settings.TMDB_API_KEY,
                        "with_genres": "16",
                        "primary_release_date.gte": f"{y_start}-01-01",
                        "primary_release_date.lte": f"{y_end}-12-31",
                        "sort_by": "popularity.desc",
                        "page": page
                    }
                )
                if res.status_code == 200:
                    results = res.json().get("results", [])
                    if not results:
                        break
                    for m in results:
                        mid = m.get("id")
                        title = m.get("title") or m.get("original_title")
                        if not mid or not title or mid in seen_ids or f"MOV-{mid}" in existing_movie_ids:
                            continue
                        seen_ids.add(mid)
                        desc = m.get("overview") or ""
                        rating = round(float(m.get("vote_average", 7.0)), 1)
                        year = parse_year(m.get("release_date"))
                        poster = f"{TMDB_IMAGE_BASE}{m.get('poster_path')}" if m.get("poster_path") else None
                        genre_names = [TMDB_MOVIE_GENRES.get(gid, "Animation") for gid in m.get("genre_ids", []) if TMDB_MOVIE_GENRES.get(gid)]
                        if "Animation" not in genre_names:
                            genre_names.insert(0, "Animation")
                        genre_str = ", ".join(genre_names)
                        pcts = compute_story_genre_percentages(title, desc, genre_str)
                        ott_prov, ott_plat, ott_url = build_default_ott(mid, is_tv=False)

                        movie_records.append({
                            "id": f"MOV-{mid}",
                            "tmdb_id": mid,
                            "imdb_id": None,
                            "title": title,
                            "average_rating": rating,
                            "description": desc,
                            "duration": None,
                            "release_year": year,
                            "poster_image_url": poster,
                            "genre": genre_str,
                            "genre_percentages": pcts,
                            "language": m.get("original_language"),
                            "director_id": None,
                            "ott_platform": ott_plat,
                            "ott_url": ott_url,
                            "ott_providers": ott_prov
                        })
                if len(movie_records) >= anim_target:
                    break
            except Exception as e:
                logger.warning(f"Error fetching animation page {page}: {e}")

    logger.info(f"Collected {len(movie_records)} Animation Movies. Now collecting ~{live_target} Live-Action Movies...")

    # 2. Fetch Live Action Movies (without genre 16) across years and sort variations
    live_start = len(movie_records)
    live_year_blocks = [
        (2023, 2026), (2020, 2022), (2017, 2019), (2014, 2016), (2010, 2013),
        (2005, 2009), (2000, 2004), (1995, 1999), (1990, 1994), (1980, 1989),
        (1970, 1979), (1950, 1969)
    ]

    for y_start, y_end in live_year_blocks:
        if len(movie_records) >= target_count:
            break
        logger.info(f"Live-action fetch block: {y_start} to {y_end} (Collected so far: {len(movie_records)}/{target_count})...")
        
        # Try both popularity.desc and vote_count.desc
        for sort_metric in ["popularity.desc", "vote_count.desc"]:
            for page in range(1, 150):
                try:
                    res = await client.get(
                        f"{TMDB_BASE_URL}/discover/movie",
                        params={
                            "api_key": settings.TMDB_API_KEY,
                            "without_genres": "16",
                            "primary_release_date.gte": f"{y_start}-01-01",
                            "primary_release_date.lte": f"{y_end}-12-31",
                            "sort_by": sort_metric,
                            "page": page
                        }
                    )
                    if res.status_code == 200:
                        results = res.json().get("results", [])
                        if not results:
                            break
                        for m in results:
                            mid = m.get("id")
                            title = m.get("title") or m.get("original_title")
                            if not mid or not title or mid in seen_ids or f"MOV-{mid}" in existing_movie_ids:
                                continue
                            seen_ids.add(mid)
                            desc = m.get("overview") or ""
                            rating = round(float(m.get("vote_average", 6.8)), 1)
                            year = parse_year(m.get("release_date"))
                            poster = f"{TMDB_IMAGE_BASE}{m.get('poster_path')}" if m.get("poster_path") else None
                            genre_names = [TMDB_MOVIE_GENRES.get(gid, "Drama") for gid in m.get("genre_ids", []) if TMDB_MOVIE_GENRES.get(gid)]
                            genre_str = ", ".join(genre_names) if genre_names else "Drama"
                            pcts = compute_story_genre_percentages(title, desc, genre_str)
                            ott_prov, ott_plat, ott_url = build_default_ott(mid, is_tv=False)

                            movie_records.append({
                                "id": f"MOV-{mid}",
                                "tmdb_id": mid,
                                "imdb_id": None,
                                "title": title,
                                "average_rating": rating,
                                "description": desc,
                                "duration": None,
                                "release_year": year,
                                "poster_image_url": poster,
                                "genre": genre_str,
                                "genre_percentages": pcts,
                                "language": m.get("original_language"),
                                "director_id": None,
                                "ott_platform": ott_plat,
                                "ott_url": ott_url,
                                "ott_providers": ott_prov
                            })
                    if len(movie_records) >= target_count:
                        break
                except Exception as e:
                    logger.warning(f"Error fetching live-action page {page}: {e}")
            if len(movie_records) >= target_count:
                break

    # Trim to exact target
    final_movies = movie_records[:target_count]
    logger.info(f"Beginning Database Insertion for {len(final_movies)} Movies in batches of 500...")

    chunk_size = 500
    inserted = 0
    for i in range(0, len(final_movies), chunk_size):
        chunk = final_movies[i:i + chunk_size]
        movie_objs = [Movie(**item) for item in chunk]
        db.bulk_save_objects(movie_objs)
        db.commit()
        inserted += len(chunk)
        logger.info(f"Committed Movies batch: {inserted}/{len(final_movies)} inserted into PostgreSQL.")

    logger.info(f"=== Successfully completed Movies Ingestion! Added {inserted} movies ===")


async def main():
    logger.info("Starting Huge Catalog Expansion Pipeline...")
    init_db()
    db = SessionLocal()
    client = httpx.AsyncClient(timeout=15.0, limits=httpx.Limits(max_connections=35, max_keepalive_connections=20))

    try:
        # 1. Expand Cinematic Universes
        await populate_cinematic_universes(client, db)

        # 2. Ingest 5,000 More Series (60% non-animation, 40% animation)
        await ingest_web_series_batch(client, db, target_count=5000, animation_ratio=0.40)

        # 3. Ingest 50,000 More Movies (60% non-animation, 40% animation)
        await ingest_movies_batch(client, db, target_count=50000, animation_ratio=0.40)

        logger.info("ALL PIPELINE STAGES COMPLETED SUCCESSFULLY!")
    finally:
        await client.aclose()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
