import re
import logging
import httpx
import concurrent.futures
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Director, Movie, WebSeries, Season, Episode
from app.services.genre_service import analyze_story_lexical

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


def get_or_create_director(db: Session, name: str, nationality: Optional[str] = None) -> Director:
    name_clean = name.strip()
    director = db.query(Director).filter(Director.name.ilike(name_clean)).first()
    if not director:
        director = Director(name=name_clean, nationality=nationality)
        db.add(director)
        db.commit()
        db.refresh(director)
    elif nationality and not director.nationality:
        director.nationality = nationality
        db.commit()
        db.refresh(director)
    return director


def fetch_tmdb_movie(client: httpx.Client, tmdb_id: int) -> Optional[Dict[str, Any]]:
    url = f"{TMDB_BASE_URL}/movie/{tmdb_id}"
    params = {"api_key": settings.TMDB_API_KEY}
    res = client.get(url, params=params)
    if res.status_code == 200:
        return res.json()
    return None


def fetch_tmdb_movie_director(client: httpx.Client, tmdb_id: int) -> Dict[str, Optional[str]]:
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
                    p_res = client.get(f"{TMDB_BASE_URL}/person/{person_id}", params=params)
                    if p_res.status_code == 200:
                        nationality = p_res.json().get("place_of_birth")
                break

    return {"name": director_name, "nationality": nationality}


def fetch_omdb_movie(client: httpx.Client, imdb_id: str) -> Optional[Dict[str, Any]]:
    if not settings.OMDB_API_KEY:
        return None
    params = {"apikey": settings.OMDB_API_KEY, "i": imdb_id}
    res = client.get(OMDB_BASE_URL, params=params)
    if res.status_code == 200:
        data = res.json()
        if data.get("Response") == "True":
            return data
    return None


def fetch_tmdb_watch_provider(client: httpx.Client, tmdb_id: int, is_tv: bool = False) -> Dict[str, Optional[str]]:
    """
    Fetches official OTT streaming link and platform name from TMDB Watch Providers.
    """
    endpoint = "tv" if is_tv else "movie"
    url = f"{TMDB_BASE_URL}/{endpoint}/{tmdb_id}/watch/providers"
    params = {"api_key": settings.TMDB_API_KEY}
    ott_url = None
    ott_platform = None

    try:
        res = client.get(url, params=params)
        if res.status_code == 200:
            data = res.json().get("results", {})
            # Prefer IN (India) or US, then any available region
            country_data = data.get("IN") or data.get("US") or (list(data.values())[0] if data else {})
            if country_data:
                ott_url = country_data.get("link")
                flatrate = country_data.get("flatrate", [])
                buy = country_data.get("buy", [])
                rent = country_data.get("rent", [])
                providers = flatrate or buy or rent
                if providers:
                    ott_platform = providers[0].get("provider_name")
    except Exception as e:
        logger.warning(f"Error fetching watch providers for {endpoint} {tmdb_id}: {e}")

    return {"ott_url": ott_url, "ott_platform": ott_platform}


def ingest_movie_by_tmdb_id(db: Session, tmdb_id: int) -> Movie:
    if not settings.TMDB_API_KEY:
        raise ValueError("TMDB_API_KEY is not configured in backend settings.")

    with httpx.Client(timeout=12.0) as client:
        tmdb_data = fetch_tmdb_movie(client, tmdb_id)
        if not tmdb_data:
            raise ValueError(f"Movie with TMDB ID {tmdb_id} was not found on TMDB.")

        imdb_id = tmdb_data.get("imdb_id")
        title = tmdb_data.get("title") or tmdb_data.get("original_title")
        avg_rating = parse_float(tmdb_data.get("vote_average"))
        description = tmdb_data.get("overview")
        duration = parse_int(tmdb_data.get("runtime"))
        release_date = tmdb_data.get("release_date")
        release_year = parse_int(release_date[:4]) if release_date and len(release_date) >= 4 else None

        tmdb_genres = [g.get("name") for g in tmdb_data.get("genres", []) if g.get("name")]
        genre = ", ".join(tmdb_genres) if tmdb_genres else None

        tmdb_langs = [l.get("english_name") or l.get("name") for l in tmdb_data.get("spoken_languages", []) if l.get("name")]
        language = ", ".join(tmdb_langs) if tmdb_langs else tmdb_data.get("original_language")

        poster_path = tmdb_data.get("poster_path")
        poster_image_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None

        director_info = fetch_tmdb_movie_director(client, tmdb_id)
        director_name = director_info.get("name")
        director_nationality = director_info.get("nationality")

        # OMDB fallback/enhancement
        omdb_data = None
        if imdb_id:
            omdb_data = fetch_omdb_movie(client, imdb_id)
        if not omdb_data and title:
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

        director_obj = None
        if director_name and director_name != "N/A":
            director_obj = get_or_create_director(db, name=director_name, nationality=director_nationality)

        # Watch providers / OTT link
        watch_info = fetch_tmdb_watch_provider(client, tmdb_id, is_tv=False)
        ott_url = watch_info.get("ott_url") or tmdb_data.get("homepage") or None
        ott_platform = watch_info.get("ott_platform")

        genre_percentages = analyze_story_lexical(title, description, genre)

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
            movie.genre_percentages = genre_percentages
            movie.language = language
            if director_obj:
                movie.director_id = director_obj.id
            if not movie.ott_url and ott_url:
                movie.ott_url = ott_url
            if not movie.ott_platform and ott_platform:
                movie.ott_platform = ott_platform
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
                genre_percentages=genre_percentages,
                language=language,
                director_id=director_obj.id if director_obj else None,
                ott_url=ott_url,
                ott_platform=ott_platform,
            )
            db.add(movie)

        db.commit()
        db.refresh(movie)
        return movie


def fetch_tmdb_tv_show(client: httpx.Client, tmdb_id: int) -> Optional[Dict[str, Any]]:
    url = f"{TMDB_BASE_URL}/tv/{tmdb_id}"
    params = {"api_key": settings.TMDB_API_KEY}
    res = client.get(url, params=params)
    if res.status_code == 200:
        return res.json()
    return None


def fetch_tmdb_tv_director(client: httpx.Client, tmdb_id: int, show_data: Dict[str, Any]) -> Dict[str, Optional[str]]:
    params = {"api_key": settings.TMDB_API_KEY}
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
    return None


def ingest_series_by_tmdb_id(db: Session, tmdb_id: int) -> WebSeries:
    if not settings.TMDB_API_KEY:
        raise ValueError("TMDB_API_KEY is not configured in backend settings.")

    with httpx.Client(timeout=15.0) as client:
        show_data = fetch_tmdb_tv_show(client, tmdb_id)
        if not show_data:
            raise ValueError(f"TV Show with TMDB ID {tmdb_id} was not found on TMDB.")

        ext_res = client.get(f"{TMDB_BASE_URL}/tv/{tmdb_id}/external_ids", params={"api_key": settings.TMDB_API_KEY})
        imdb_id = ext_res.json().get("imdb_id") if ext_res.status_code == 200 else None

        title = show_data.get("name") or show_data.get("original_name")
        avg_rating = parse_float(show_data.get("vote_average"))
        description = show_data.get("overview")
        first_air_date = show_data.get("first_air_date")
        release_year = parse_int(first_air_date[:4]) if first_air_date and len(first_air_date) >= 4 else None
        total_seasons = show_data.get("number_of_seasons") or 1

        genre_names = [g.get("name") for g in show_data.get("genres", []) if g.get("name")]
        genre = ", ".join(genre_names) if genre_names else None

        lang_names = [l.get("english_name") or l.get("name") for l in show_data.get("spoken_languages", []) if l.get("name")]
        language = ", ".join(lang_names) if lang_names else show_data.get("original_language")

        poster_path = show_data.get("poster_path")
        poster_image_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None

        dir_info = fetch_tmdb_tv_director(client, tmdb_id, show_data)
        director_name = dir_info.get("name")
        director_nationality = dir_info.get("nationality")
        director_obj = None
        if director_name:
            director_obj = get_or_create_director(db, name=director_name, nationality=director_nationality)

        # Watch providers / OTT link for Series
        watch_info = fetch_tmdb_watch_provider(client, tmdb_id, is_tv=True)
        ott_url = watch_info.get("ott_url") or show_data.get("homepage") or None
        ott_platform = watch_info.get("ott_platform")

        series_id = f"SER-{tmdb_id}"
        genre_percentages = analyze_story_lexical(title, description, genre)

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
            series.genre_percentages = genre_percentages
            series.language = language
            if director_obj:
                series.director_id = director_obj.id
            if not series.ott_url and ott_url:
                series.ott_url = ott_url
            if not series.ott_platform and ott_platform:
                series.ott_platform = ott_platform
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
                genre_percentages=genre_percentages,
                language=language,
                director_id=director_obj.id if director_obj else None,
                ott_url=ott_url,
                ott_platform=ott_platform,
            )
            db.add(series)

        db.commit()
        db.refresh(series)

        # Process seasons & episodes
        raw_seasons = show_data.get("seasons", [])
        canonical_seasons = [s for s in raw_seasons if s.get("season_number", 0) > 0]
        if not canonical_seasons:
            canonical_seasons = raw_seasons

        print(f"    --> [TMDB] Fetching {len(canonical_seasons)} seasons in parallel for '{title}'...", flush=True)

        def fetch_season_details_task(s_meta):
            s_num = s_meta.get("season_number", 1)
            try:
                s_det = fetch_tmdb_season_details(client, tmdb_id, s_num)
                return (s_meta, s_det)
            except Exception as ex:
                logger.warning(f"Error fetching season {s_num} for TMDB {tmdb_id}: {ex}")
                return (s_meta, None)

        max_workers = min(len(canonical_seasons), 8) or 1
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            fetched_seasons = list(executor.map(fetch_season_details_task, canonical_seasons))

        # Bulk load existing seasons for this series to eliminate N+1 queries
        existing_seasons = {
            s.season_id: s for s in db.query(Season).filter(Season.series_id == series_id).all()
        }

        # Bulk load existing episodes for all seasons in this series
        target_season_ids = [f"SEAS-{tmdb_id}-{s_m.get('season_number', 1)}" for s_m, _ in fetched_seasons]
        existing_episodes = {
            ep.episode_id: ep for ep in db.query(Episode).filter(Episode.season_id.in_(target_season_ids)).all()
        }

        total_episodes_saved = 0
        for s_meta, s_detail in fetched_seasons:
            season_num = s_meta.get("season_number", 1)
            season_id = f"SEAS-{tmdb_id}-{season_num}"

            if not s_detail:
                continue

            s_air_date = s_detail.get("air_date") or s_meta.get("air_date")
            s_year = parse_int(s_air_date[:4]) if s_air_date and len(s_air_date) >= 4 else release_year
            s_poster = s_detail.get("poster_path")
            s_poster_url = f"https://image.tmdb.org/t/p/w500{s_poster}" if s_poster else poster_image_url
            episodes_list = s_detail.get("episodes", [])
            ep_count = len(episodes_list)

            season = existing_seasons.get(season_id)
            if season:
                season.season_number = season_num
                season.release_year = s_year
                season.title = s_detail.get("name") or f"Season {season_num}"
                season.description = s_detail.get("overview")
                season.poster_image_url = s_poster_url
                season.episode_count = ep_count
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
                existing_seasons[season_id] = season

            # Episodes
            for ep_data in episodes_list:
                ep_num = ep_data.get("episode_number", 1)
                ep_id = f"EP-{tmdb_id}-{season_num}-{ep_num}"
                ep_title = ep_data.get("name") or f"Episode {ep_num}"
                ep_runtime = parse_int(ep_data.get("runtime"))
                ep_overview = ep_data.get("overview")
                ep_still = ep_data.get("still_path")
                ep_still_url = f"https://image.tmdb.org/t/p/w500{ep_still}" if ep_still else None
                ep_rating = parse_float(ep_data.get("vote_average"))
                ep_air = ep_data.get("air_date")
                ep_tmdb = ep_data.get("id")

                episode = existing_episodes.get(ep_id)
                if episode:
                    episode.title = ep_title
                    episode.duration = ep_runtime
                    episode.description = ep_overview
                    episode.still_image_url = ep_still_url
                    episode.average_rating = ep_rating
                    episode.release_date = ep_air
                else:
                    episode = Episode(
                        episode_id=ep_id,
                        season_id=season_id,
                        episode_number=ep_num,
                        title=ep_title,
                        duration=ep_runtime,
                        description=ep_overview,
                        still_image_url=ep_still_url,
                        average_rating=ep_rating,
                        release_date=ep_air,
                        tmdb_id=ep_tmdb,
                    )
                    db.add(episode)
                    existing_episodes[ep_id] = episode
                total_episodes_saved += 1

        db.commit()
        print(f"    --> [TMDB] Saved {len(fetched_seasons)} seasons and {total_episodes_saved} episodes for '{title}'.", flush=True)
        db.refresh(series)
        return series
