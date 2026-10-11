import json
import logging
import random
import httpx
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.db.session import get_db
from app.db.models import Movie, Director, CinematicUniverse, UniverseContent, User, WebSeries, Season, Episode
from app.api.deps import get_admin_or_owner_user
from app.schemas.movies import (
    MovieResponse,
    MoodRequest,
    MoodResponse,
    MovieCategorySection,
    PaginatedCategoryMovies,
    FilterOptionsResponse,
    CinematicUniverseResponse,
    UniverseContentResponse,
    CreateCinematicUniverseRequest,
    AddUniverseContentRequest,
    BulkAddUniverseContentRequest,
    UnifiedSearchResponse,
    IngestTmdbRequest,
    IngestTmdbResponse,
    BulkIngestTmdbRequest,
    BulkIngestTmdbResponse,
    BulkIngestTmdbResultItem,
    UpdateCinematicUniverseRequest,
    ReorderUniverseContentsRequest,
    UpdateOttUrlRequest
)
from app.services.tmdb_service import ingest_movie_by_tmdb_id, ingest_series_by_tmdb_id
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


def series_to_movie_response(s: WebSeries) -> MovieResponse:
    return MovieResponse(
        id=s.series_id,
        tmdb_id=s.tmdb_id,
        imdb_id=s.imdb_id,
        title=s.title,
        average_rating=s.average_rating,
        description=s.description,
        duration=None,
        release_year=s.release_year,
        poster_image_url=s.poster_image_url,
        genre=s.genre,
        language=s.language,
        director_id=s.director_id,
        director=s.director,
        content_type="web_series",
        total_seasons=s.total_seasons,
        ott_url=getattr(s, "ott_url", None),
        ott_platform=getattr(s, "ott_platform", None),
    )


def movie_to_movie_response(m: Movie) -> MovieResponse:
    return MovieResponse(
        id=m.id,
        tmdb_id=m.tmdb_id,
        imdb_id=m.imdb_id,
        title=m.title,
        average_rating=m.average_rating,
        description=m.description,
        duration=m.duration,
        release_year=m.release_year,
        poster_image_url=m.poster_image_url,
        genre=m.genre,
        language=m.language,
        director_id=m.director_id,
        director=m.director,
        content_type="movie",
        total_seasons=None,
        ott_url=getattr(m, "ott_url", None),
        ott_platform=getattr(m, "ott_platform", None),
    )


@router.get("/", response_model=List[MovieResponse], summary="Get All Movies")
def get_all_movies(db: Session = Depends(get_db)):
    movies = db.query(Movie).options(joinedload(Movie.director)).order_by(Movie.id.desc()).all()
    return [movie_to_movie_response(m) for m in movies]


@router.get("/filter-options", response_model=FilterOptionsResponse, summary="Get Filter Options for Genre, Language, Years & Directors across Movies and TV Shows")
def get_filter_options(db: Session = Depends(get_db)):
    movies = db.query(Movie).options(joinedload(Movie.director)).all()
    series = db.query(WebSeries).options(joinedload(WebSeries.director)).all()
    
    genres_set = set()
    languages_set = set()
    years_set = set()
    
    for item in list(movies) + list(series):
        if item.genre:
            for g in item.genre.split(","):
                g_clean = g.strip()
                if g_clean:
                    genres_set.add(g_clean)
        if item.language:
            for l in item.language.split(","):
                l_clean = l.strip()
                if l_clean:
                    languages_set.add(l_clean)
        if item.release_year:
            years_set.add(item.release_year)
                    
    directors = db.query(Director).order_by(Director.name).all()
    
    return FilterOptionsResponse(
        genres=sorted(list(genres_set)),
        languages=sorted(list(languages_set)),
        years=sorted(list(years_set), reverse=True),
        directors=directors
    )


@router.get("/search", response_model=List[MovieResponse], summary="Search and Filter Movies and TV Shows by Keyword, Genre, Language, Year, Director, and Content Type")
def search_movies(
    query: Optional[str] = None,
    genre: Optional[str] = None,
    language: Optional[str] = None,
    release_year: Optional[int] = None,
    director_id: Optional[int] = None,
    content_type: Optional[str] = None,  # 'all', 'movie', 'web_series'
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    db: Session = Depends(get_db)
):
    results: List[MovieResponse] = []
    normalized_type = (content_type or "all").lower().strip()
    include_movies = normalized_type in ("all", "movie")
    include_series = normalized_type in ("all", "web_series", "series", "tv")

    if include_movies:
        q_m = db.query(Movie).options(joinedload(Movie.director))
        if director_id and director_id > 0:
            q_m = q_m.filter(Movie.director_id == director_id)
        if genre and genre.upper() != "ALL":
            q_m = q_m.filter(Movie.genre.ilike(f"%{genre}%"))
        if language and language.upper() != "ALL":
            q_m = q_m.filter(Movie.language.ilike(f"%{language}%"))
        if release_year and release_year > 0:
            q_m = q_m.filter(Movie.release_year == release_year)
        if query and query.strip():
            s = query.strip().lower()
            q_m = q_m.filter(
                or_(
                    Movie.title.ilike(f"%{s}%"),
                    Movie.description.ilike(f"%{s}%"),
                    Movie.genre.ilike(f"%{s}%")
                )
            )
        for m in q_m.all():
            results.append(movie_to_movie_response(m))

    if include_series:
        q_s = db.query(WebSeries).options(joinedload(WebSeries.director))
        if director_id and director_id > 0:
            q_s = q_s.filter(WebSeries.director_id == director_id)
        if genre and genre.upper() != "ALL":
            q_s = q_s.filter(WebSeries.genre.ilike(f"%{genre}%"))
        if language and language.upper() != "ALL":
            q_s = q_s.filter(WebSeries.language.ilike(f"%{language}%"))
        if release_year and release_year > 0:
            q_s = q_s.filter(WebSeries.release_year == release_year)
        if query and query.strip():
            s = query.strip().lower()
            q_s = q_s.filter(
                or_(
                    WebSeries.title.ilike(f"%{s}%"),
                    WebSeries.description.ilike(f"%{s}%"),
                    WebSeries.genre.ilike(f"%{s}%")
                )
            )
        for s in q_s.all():
            results.append(series_to_movie_response(s))

    # Sort combined results by average rating (desc) then release year (desc)
    results.sort(
        key=lambda x: (
            x.average_rating if x.average_rating is not None else 0,
            x.release_year if x.release_year is not None else 0,
        ),
        reverse=True
    )

    if offset is not None and offset >= 0:
        results = results[offset:]
    if limit is not None and limit > 0:
        results = results[:limit]

    return results


def filter_category_list(
    items: List[MovieResponse],
    category_type: str,
    director_name: Optional[str] = None,
    genre_name: Optional[str] = None,
    release_year: Optional[int] = None
) -> List[MovieResponse]:
    if category_type == "top_rated":
        top = [m for m in items if m.average_rating is not None and m.average_rating >= 8.5]
        return sorted(top, key=lambda x: x.average_rating or 0, reverse=True)
    elif category_type in ["genre", "random_genre"] and genre_name:
        return [m for m in items if m.genre and genre_name.lower() in m.genre.lower()]
    elif category_type in ["year", "random_year"] and release_year:
        return [m for m in items if m.release_year and int(m.release_year) == int(release_year)]
    elif category_type == "director" and director_name:
        return [m for m in items if m.director and m.director.name == director_name]
    else:  # 'all'
        return sorted(items, key=lambda x: x.id, reverse=True)


@router.get("/categories", response_model=List[MovieCategorySection], summary="Get Categorized Movie & Series Collections")
def get_movie_categories(db: Session = Depends(get_db)):
    movies_raw = db.query(Movie).options(joinedload(Movie.director)).all()
    series_raw = db.query(WebSeries).options(joinedload(WebSeries.director)).all()

    all_items: List[MovieResponse] = (
        [movie_to_movie_response(m) for m in movies_raw] +
        [series_to_movie_response(s) for s in series_raw]
    )

    if not all_items:
        return []

    sections = []

    # 1. Binge-Worthy TV Shows & Web Series Row (if series exist)
    if series_raw:
        series_items = [series_to_movie_response(s) for s in series_raw]
        series_items.sort(key=lambda x: x.average_rating or 0, reverse=True)
        sections.append(
            MovieCategorySection(
                title="📺 Binge-Worthy TV Shows & Web Series",
                description="Acclaimed multi-season dramas, thrillers, and iconic series",
                category_type="trending_series",
                has_more=len(series_items) > 5,
                movies=series_items[:5]
            )
        )

    # 2. Top Rated Masterpieces
    top_rated_full = filter_category_list(all_items, "top_rated")
    if top_rated_full:
        sections.append(
            MovieCategorySection(
                title="⭐ Top Rated Masterpieces",
                description="Highest user rated critically acclaimed films and series",
                category_type="top_rated",
                has_more=len(top_rated_full) > 5,
                movies=top_rated_full[:5]
            )
        )

    # 3. Random Genre Row
    all_genres = set()
    for item in all_items:
        if item.genre:
            for g in item.genre.split(","):
                g_clean = g.strip()
                if g_clean:
                    all_genres.add(g_clean)
    if all_genres:
        selected_genre = random.choice(sorted(list(all_genres)))
        genre_items = filter_category_list(all_items, "random_genre", genre_name=selected_genre)
        if genre_items:
            sections.append(
                MovieCategorySection(
                    title=f"🎬 Genre Spotlight: {selected_genre}",
                    description=f"Curated selection of top {selected_genre} entertainment in our collection",
                    category_type="random_genre",
                    genre_name=selected_genre,
                    has_more=len(genre_items) > 5,
                    movies=genre_items[:5]
                )
            )

    # 4. Random Year Row
    all_years = list(set([item.release_year for item in all_items if item.release_year]))
    if all_years:
        selected_year = random.choice(sorted(all_years, reverse=True))
        year_items = filter_category_list(all_items, "random_year", release_year=selected_year)
        if year_items:
            sections.append(
                MovieCategorySection(
                    title=f"📅 Time Capsule: Released in {selected_year}",
                    description=f"Explore titles released in the year {selected_year}",
                    category_type="random_year",
                    release_year=selected_year,
                    has_more=len(year_items) > 5,
                    movies=year_items[:5]
                )
            )

    # 5. Director Spotlight (if directors exist)
    directors = [item.director.name for item in all_items if item.director and item.director.name]
    if directors:
        selected_director = random.choice(list(set(directors)))
        director_items = filter_category_list(all_items, "director", director_name=selected_director)
        if director_items:
            sections.append(
                MovieCategorySection(
                    title=f"🎥 Director / Creator Spotlight: {selected_director}",
                    description=f"Masterpieces crafted by {selected_director}",
                    category_type="director",
                    director_name=selected_director,
                    has_more=len(director_items) > 5,
                    movies=director_items[:5]
                )
            )

    # 6. Random Cinematic Universe Row
    universes = db.query(CinematicUniverse).options(joinedload(CinematicUniverse.contents)).all()
    valid_universes = [u for u in universes if u.contents]
    if valid_universes:
        selected_universe = random.choice(valid_universes)
        uni_res = build_universe_response(selected_universe, db)
        uni_movies = [c.movie for c in uni_res.contents if c.movie]
        if uni_movies:
            sections.append(
                MovieCategorySection(
                    title=f"🌌 Cinematic Universe: {selected_universe.name}",
                    description=selected_universe.description or f"Story titles in {selected_universe.name}",
                    category_type="universe",
                    universe_name=selected_universe.name,
                    universe_id=selected_universe.id,
                    has_more=len(uni_movies) > 5,
                    movies=uni_movies[:5]
                )
            )

    return sections


@router.get("/category-movies", response_model=PaginatedCategoryMovies, summary="Fetch Paginated Category Titles on Horizontal Scroll")
def get_category_movies(
    category_type: str,
    offset: int = 0,
    limit: int = 5,
    genre_name: Optional[str] = None,
    release_year: Optional[int] = None,
    director_name: Optional[str] = None,
    universe_id: Optional[int] = None,
    universe_name: Optional[str] = None,
    db: Session = Depends(get_db)
):
    if category_type == "universe" and universe_id:
        uni = db.query(CinematicUniverse).options(joinedload(CinematicUniverse.contents)).filter(CinematicUniverse.id == universe_id).first()
        if uni:
            uni_res = build_universe_response(uni, db)
            filtered = [c.movie for c in uni_res.contents if c.movie]
        else:
            filtered = []
    elif category_type == "trending_series":
        series = db.query(WebSeries).options(joinedload(WebSeries.director)).all()
        series_items = [series_to_movie_response(s) for s in series]
        series_items.sort(key=lambda x: x.average_rating or 0, reverse=True)
        filtered = series_items
    else:
        movies_raw = db.query(Movie).options(joinedload(Movie.director)).all()
        series_raw = db.query(WebSeries).options(joinedload(WebSeries.director)).all()
        all_items = (
            [movie_to_movie_response(m) for m in movies_raw] +
            [series_to_movie_response(s) for s in series_raw]
        )
        filtered = filter_category_list(
            all_items,
            category_type=category_type,
            genre_name=genre_name,
            release_year=release_year,
            director_name=director_name
        )
    
    paginated_movies = filtered[offset : offset + limit]
    has_more = (offset + limit) < len(filtered)

    return PaginatedCategoryMovies(
        category_type=category_type,
        genre_name=genre_name,
        release_year=release_year,
        director_name=director_name,
        universe_name=universe_name,
        universe_id=universe_id,
        offset=offset,
        limit=limit,
        has_more=has_more,
        movies=paginated_movies
    )


@router.post("/mood-recommendation", response_model=MoodResponse, summary="Get AI Movie Recommendations based on User Mood")
def get_mood_recommendation(payload: MoodRequest, db: Session = Depends(get_db)):
    mood = payload.mood.strip()
    if not mood:
        raise HTTPException(status_code=400, detail="Mood query cannot be empty")

    movies_raw = db.query(Movie).options(joinedload(Movie.director)).all()
    series_raw = db.query(WebSeries).options(joinedload(WebSeries.director)).all()
    all_items: List[MovieResponse] = (
        [movie_to_movie_response(m) for m in movies_raw] +
        [series_to_movie_response(s) for s in series_raw]
    )
    if not all_items:
        raise HTTPException(status_code=404, detail="No titles available in database")

    # Format compact catalog representation for LLM context
    catalog = [
        {
            "id": m.id,
            "title": m.title,
            "type": m.content_type,
            "year": m.release_year,
            "rating": m.average_rating,
            "director": m.director.name if m.director else None,
            "description": m.description[:200] if m.description else ""
        }
        for m in all_items
    ]

    system_prompt = (
        "You are an expert AI movie and TV show recommendation engine for PrimeFlix. "
        "Analyze the user's current mood or situation description and pick the best matching title IDs from the provided catalog.\n\n"
        "Catalog:\n"
        f"{json.dumps(catalog, indent=2)}\n\n"
        "INSTRUCTIONS:\n"
        "1. Select between 1 to 5 title IDs from the catalog above that best match the user's mood.\n"
        "2. Provide a short, captivating 2-sentence explanation of why these titles fit the mood.\n"
        "3. You MUST respond with ONLY a valid JSON object in the following format:\n"
        '{\n  "explanation": "Your explanation here...",\n  "recommended_ids": ["MOV-1", "SER-1396"]\n}'
    )

    user_prompt = f"User Mood / Query: {mood}"

    recommended_movies = []
    explanation = "Here are top entertainment recommendations tailored for your mood."

    # Call Gemini REST API using httpx
    if settings.GEMINI_API_KEY:
        try:
            # Using Gemini v1beta endpoint
            gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={settings.GEMINI_API_KEY}"
            request_body = {
                "contents": [
                    {
                        "parts": [
                            {"text": f"{system_prompt}\n\n{user_prompt}"}
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.7,
                    "response_mime_type": "application/json"
                }
            }

            with httpx.Client(timeout=15.0) as client:
                res = client.post(gemini_url, json=request_body)
                if res.status_code == 200:
                    res_json = res.json()
                    candidates = res_json.get("candidates", [])
                    if candidates:
                        text_response = candidates[0]["content"]["parts"][0]["text"]
                        parsed_ai = json.loads(text_response)
                        explanation = parsed_ai.get("explanation", explanation)
                        rec_ids = parsed_ai.get("recommended_ids", [])
                        
                        id_map = {m.id: m for m in all_items}
                        for r_id in rec_ids:
                            if r_id in id_map:
                                recommended_movies.append(id_map[r_id])
                else:
                    logger.error(f"Gemini API returned status {res.status_code}: {res.text}")
        except Exception as e:
            logger.error(f"Error invoking Gemini API: {e}")

    # Fallback if AI didn't return matches or API key wasn't available
    if not recommended_movies:
        # Simple keyword matching fallback
        words = mood.lower().split()
        matched = []
        for m in all_items:
            text = f"{m.title} {m.description or ''}".lower()
            if any(w in text for w in words if len(w) > 3):
                matched.append(m)
        recommended_movies = matched[:4] if matched else all_items[:4]
        explanation = f"Based on your mood query '{mood}', here are titles you might enjoy!"

    return MoodResponse(
        mood=mood,
        explanation=explanation,
        movies=recommended_movies
    )


def build_universe_response(
    universe: CinematicUniverse,
    db: Session,
    limit: Optional[int] = None,
    offset: Optional[int] = None
) -> CinematicUniverseResponse:
    movie_ids = [c.content_id for c in universe.contents if c.content_type == "movie" or c.content_id.startswith("MOV-")]
    movies_map = {}
    if movie_ids:
        movies = db.query(Movie).options(joinedload(Movie.director)).filter(Movie.id.in_(movie_ids)).all()
        for m in movies:
            movies_map[m.id] = movie_to_movie_response(m)

    series_ids = [c.content_id for c in universe.contents if c.content_type in ("web_series", "series") or c.content_id.startswith("SER-")]
    if series_ids:
        series_items = db.query(WebSeries).options(joinedload(WebSeries.director)).filter(WebSeries.series_id.in_(series_ids)).all()
        for s in series_items:
            movies_map[s.series_id] = series_to_movie_response(s)

    content_responses = []
    for c in universe.contents:
        item = movies_map.get(c.content_id)
        content_responses.append(
            UniverseContentResponse(
                id=c.id,
                universe_id=c.universe_id,
                content_id=c.content_id,
                content_type=c.content_type,
                order_in_universe=c.order_in_universe,
                movie=item
            )
        )

    content_responses.sort(
        key=lambda x: (
            x.movie.release_year if (x.movie and x.movie.release_year is not None) else 9999,
            x.order_in_universe if x.order_in_universe is not None else 9999,
            x.id
        )
    )

    if offset is not None and offset >= 0:
        content_responses = content_responses[offset:]
    if limit is not None and limit > 0:
        content_responses = content_responses[:limit]

    return CinematicUniverseResponse(
        id=universe.id,
        name=universe.name,
        description=universe.description,
        contents=content_responses
    )


@router.get("/unified-search", response_model=UnifiedSearchResponse, summary="Search Movies, TV Series, and Cinematic Universes grouped by type")
def unified_search(
    query: Optional[str] = None,
    genre: Optional[str] = None,
    language: Optional[str] = None,
    release_year: Optional[int] = None,
    director_id: Optional[int] = None,
    content_type: Optional[str] = None,  # 'all', 'movie', 'web_series', 'universe'
    db: Session = Depends(get_db)
):
    normalized_type = (content_type or "all").lower().strip()
    include_movies = normalized_type in ("all", "movie", "movies")
    include_series = normalized_type in ("all", "web_series", "series", "tv", "shows")
    include_universes = normalized_type in ("all", "universe", "universes")

    movies_res: List[MovieResponse] = []
    series_res: List[MovieResponse] = []
    universes_res: List[CinematicUniverseResponse] = []

    # 1. Movies
    if include_movies:
        q_m = db.query(Movie).options(joinedload(Movie.director))
        if director_id and director_id > 0:
            q_m = q_m.filter(Movie.director_id == director_id)
        if genre and genre.upper() != "ALL":
            q_m = q_m.filter(Movie.genre.ilike(f"%{genre}%"))
        if language and language.upper() != "ALL":
            q_m = q_m.filter(Movie.language.ilike(f"%{language}%"))
        if release_year and release_year > 0:
            q_m = q_m.filter(Movie.release_year == release_year)
        if query and query.strip():
            s = query.strip().lower()
            q_m = q_m.filter(
                or_(
                    Movie.title.ilike(f"%{s}%"),
                    Movie.description.ilike(f"%{s}%"),
                    Movie.genre.ilike(f"%{s}%")
                )
            )
        movies_res = [movie_to_movie_response(m) for m in q_m.all()]
        movies_res.sort(
            key=lambda x: (x.average_rating if x.average_rating is not None else 0, x.release_year if x.release_year is not None else 0),
            reverse=True
        )

    # 2. TV Series & Web Series
    if include_series:
        q_s = db.query(WebSeries).options(joinedload(WebSeries.director))
        if director_id and director_id > 0:
            q_s = q_s.filter(WebSeries.director_id == director_id)
        if genre and genre.upper() != "ALL":
            q_s = q_s.filter(WebSeries.genre.ilike(f"%{genre}%"))
        if language and language.upper() != "ALL":
            q_s = q_s.filter(WebSeries.language.ilike(f"%{language}%"))
        if release_year and release_year > 0:
            q_s = q_s.filter(WebSeries.release_year == release_year)
        if query and query.strip():
            s = query.strip().lower()
            q_s = q_s.filter(
                or_(
                    WebSeries.title.ilike(f"%{s}%"),
                    WebSeries.description.ilike(f"%{s}%"),
                    WebSeries.genre.ilike(f"%{s}%")
                )
            )
        series_res = [series_to_movie_response(s) for s in q_s.all()]
        series_res.sort(
            key=lambda x: (x.average_rating if x.average_rating is not None else 0, x.release_year if x.release_year is not None else 0),
            reverse=True
        )

    # 3. Cinematic Universes
    if include_universes:
        all_universes = db.query(CinematicUniverse).options(joinedload(CinematicUniverse.contents)).all()
        for u in all_universes:
            u_res = build_universe_response(u, db)
            matches = True
            if query and query.strip():
                s = query.strip().lower()
                name_match = s in u.name.lower() or (u.description and s in u.description.lower())
                item_match = any((c.movie and s in c.movie.title.lower()) for c in u_res.contents)
                if not (name_match or item_match):
                    matches = False

            if genre and genre.upper() != "ALL" and matches:
                genre_match = any((c.movie and c.movie.genre and genre.lower() in c.movie.genre.lower()) for c in u_res.contents)
                if not genre_match:
                    matches = False

            if language and language.upper() != "ALL" and matches:
                lang_match = any((c.movie and c.movie.language and language.lower() in c.movie.language.lower()) for c in u_res.contents)
                if not lang_match:
                    matches = False

            if release_year and release_year > 0 and matches:
                year_match = any((c.movie and c.movie.release_year == release_year) for c in u_res.contents)
                if not year_match:
                    matches = False

            if director_id and director_id > 0 and matches:
                dir_match = any((c.movie and c.movie.director_id == director_id) for c in u_res.contents)
                if not dir_match:
                    matches = False

            if matches:
                universes_res.append(u_res)

    total = len(movies_res) + len(series_res) + len(universes_res)

    return UnifiedSearchResponse(
        movies=movies_res,
        series=series_res,
        universes=universes_res,
        total_count=total
    )


@router.get("/universes/all", response_model=List[CinematicUniverseResponse], summary="Get All Cinematic Universes")
def get_cinematic_universes(
    limit: Optional[int] = 10,
    offset: Optional[int] = 0,
    db: Session = Depends(get_db)
):
    universes = db.query(CinematicUniverse).options(joinedload(CinematicUniverse.contents)).all()
    return [build_universe_response(u, db, limit=limit, offset=offset) for u in universes]


@router.get("/universes/{universe_id}", response_model=CinematicUniverseResponse, summary="Get Cinematic Universe Details")
def get_cinematic_universe_detail(
    universe_id: int,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    db: Session = Depends(get_db)
):
    universe = db.query(CinematicUniverse).options(joinedload(CinematicUniverse.contents)).filter(CinematicUniverse.id == universe_id).first()
    if not universe:
        raise HTTPException(status_code=404, detail="Cinematic Universe not found")
    return build_universe_response(universe, db, limit=limit, offset=offset)


@router.get("/universes/{universe_id}/contents", response_model=CinematicUniverseResponse, summary="Get Paginated Contents of a Cinematic Universe")
def get_cinematic_universe_contents(
    universe_id: int,
    limit: int = 10,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    universe = db.query(CinematicUniverse).options(joinedload(CinematicUniverse.contents)).filter(CinematicUniverse.id == universe_id).first()
    if not universe:
        raise HTTPException(status_code=404, detail="Cinematic Universe not found")
    return build_universe_response(universe, db, limit=limit, offset=offset)


@router.post("/universes", response_model=CinematicUniverseResponse, summary="Create a Cinematic Universe (Admin/Owner only)")
def create_cinematic_universe(
    req: CreateCinematicUniverseRequest,
    current_user: User = Depends(get_admin_or_owner_user),
    db: Session = Depends(get_db)
):
    existing = db.query(CinematicUniverse).filter(CinematicUniverse.name.ilike(req.name.strip())).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A Cinematic Universe with this name already exists.")

    universe = CinematicUniverse(
        name=req.name.strip(),
        description=req.description.strip() if req.description else None
    )
    db.add(universe)
    db.commit()
    db.refresh(universe)
    return build_universe_response(universe, db)


@router.post("/universes/{universe_id}/contents", response_model=CinematicUniverseResponse, summary="Add Content to Cinematic Universe (Admin/Owner only)")
def add_content_to_universe(
    universe_id: int,
    req: AddUniverseContentRequest,
    current_user: User = Depends(get_admin_or_owner_user),
    db: Session = Depends(get_db)
):
    universe = db.query(CinematicUniverse).options(joinedload(CinematicUniverse.contents)).filter(CinematicUniverse.id == universe_id).first()
    if not universe:
        raise HTTPException(status_code=404, detail="Cinematic Universe not found")

    actual_type = "movie"
    if req.content_id.startswith("SER-") or req.content_type in ("web_series", "series"):
        series = db.query(WebSeries).filter(WebSeries.series_id == req.content_id).first()
        if not series:
            raise HTTPException(status_code=404, detail=f"Web Series with ID '{req.content_id}' not found.")
        actual_type = "web_series"
    else:
        movie = db.query(Movie).filter(Movie.id == req.content_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail=f"Movie with ID '{req.content_id}' not found.")
        actual_type = "movie"

    existing = db.query(UniverseContent).filter(
        UniverseContent.universe_id == universe_id,
        UniverseContent.content_id == req.content_id
    ).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This content is already added to this Cinematic Universe.")

    order_val = req.order_in_universe
    if order_val is None:
        max_order = max([c.order_in_universe for c in universe.contents if c.order_in_universe is not None], default=0)
        order_val = max_order + 1

    content = UniverseContent(
        universe_id=universe_id,
        content_id=req.content_id,
        content_type=actual_type,
        order_in_universe=order_val
    )
    db.add(content)
    db.commit()

    universe = db.query(CinematicUniverse).options(joinedload(CinematicUniverse.contents)).filter(CinematicUniverse.id == universe_id).first()
    return build_universe_response(universe, db)


@router.post("/universes/{universe_id}/contents/bulk", response_model=CinematicUniverseResponse, summary="Bulk Add Multiple Contents to Cinematic Universe (Admin/Owner only)")
def bulk_add_contents_to_universe(
    universe_id: int,
    req: BulkAddUniverseContentRequest,
    current_user: User = Depends(get_admin_or_owner_user),
    db: Session = Depends(get_db)
):
    universe = db.query(CinematicUniverse).options(joinedload(CinematicUniverse.contents)).filter(CinematicUniverse.id == universe_id).first()
    if not universe:
        raise HTTPException(status_code=404, detail="Cinematic Universe not found")

    if not req.content_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No content IDs provided.")

    existing_content_ids = {c.content_id for c in universe.contents}
    max_order = max([c.order_in_universe for c in universe.contents if c.order_in_universe is not None], default=0)

    added_count = 0
    for content_id in req.content_ids:
        if content_id in existing_content_ids:
            continue

        actual_type = "movie"
        if content_id.startswith("SER-") or (req.content_type in ("web_series", "series")):
            series = db.query(WebSeries).filter(WebSeries.series_id == content_id).first()
            if not series:
                continue
            actual_type = "web_series"
        else:
            movie = db.query(Movie).filter(Movie.id == content_id).first()
            if not movie:
                continue
            actual_type = "movie"

        max_order += 1
        new_content = UniverseContent(
            universe_id=universe_id,
            content_id=content_id,
            content_type=actual_type,
            order_in_universe=max_order
        )
        db.add(new_content)
        existing_content_ids.add(content_id)
        added_count += 1

    db.commit()

    universe = db.query(CinematicUniverse).options(joinedload(CinematicUniverse.contents)).filter(CinematicUniverse.id == universe_id).first()
    return build_universe_response(universe, db)



@router.delete("/universes/{universe_id}/contents/{content_id}", response_model=CinematicUniverseResponse, summary="Remove Content from Cinematic Universe (Admin/Owner only)")
def remove_content_from_universe(
    universe_id: int,
    content_id: str,
    current_user: User = Depends(get_admin_or_owner_user),
    db: Session = Depends(get_db)
):
    universe = db.query(CinematicUniverse).options(joinedload(CinematicUniverse.contents)).filter(CinematicUniverse.id == universe_id).first()
    if not universe:
        raise HTTPException(status_code=404, detail="Cinematic Universe not found")

    existing = db.query(UniverseContent).filter(
        UniverseContent.universe_id == universe_id,
        UniverseContent.content_id == content_id
    ).first()
    if not existing:
        raise HTTPException(status_code=404, detail="Content not found in this Cinematic Universe.")

    db.delete(existing)
    db.commit()

    universe = db.query(CinematicUniverse).options(joinedload(CinematicUniverse.contents)).filter(CinematicUniverse.id == universe_id).first()
    return build_universe_response(universe, db)


@router.delete("/universes/{universe_id}", summary="Delete Cinematic Universe (Admin/Owner only)")
def delete_cinematic_universe(
    universe_id: int,
    current_user: User = Depends(get_admin_or_owner_user),
    db: Session = Depends(get_db)
):
    universe = db.query(CinematicUniverse).filter(CinematicUniverse.id == universe_id).first()
    if not universe:
        raise HTTPException(status_code=404, detail="Cinematic Universe not found")

    db.delete(universe)
    db.commit()
    return {"message": "Cinematic Universe deleted successfully"}


@router.post("/ingest-tmdb", response_model=IngestTmdbResponse, summary="Ingest Movie or TV Series by TMDB ID (Admin/Owner only)")
def ingest_from_tmdb(
    req: IngestTmdbRequest,
    current_user: User = Depends(get_admin_or_owner_user),
    db: Session = Depends(get_db)
):
    c_type = (req.content_type or "movie").lower().strip()
    try:
        if c_type in ("web_series", "tv", "series"):
            series = ingest_series_by_tmdb_id(db, req.tmdb_id)
            return IngestTmdbResponse(
                message=f"TV Series '{series.title}' ({series.total_seasons} seasons) successfully ingested!",
                content_id=series.series_id,
                content_type="web_series",
                title=series.title,
                poster_image_url=series.poster_image_url,
                movie=series_to_movie_response(series)
            )
        else:
            movie = ingest_movie_by_tmdb_id(db, req.tmdb_id)
            return IngestTmdbResponse(
                message=f"Movie '{movie.title}' successfully ingested!",
                content_id=movie.id,
                content_type="movie",
                title=movie.title,
                poster_image_url=movie.poster_image_url,
                movie=movie_to_movie_response(movie)
            )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Error ingesting TMDB ID {req.tmdb_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to ingest from TMDB: {str(e)}")


@router.post("/ingest-tmdb-bulk", response_model=BulkIngestTmdbResponse, summary="Bulk Ingest Multiple Movies and/or Series by TMDB IDs (Admin/Owner only)")
def ingest_from_tmdb_bulk(
    req: BulkIngestTmdbRequest,
    current_user: User = Depends(get_admin_or_owner_user),
    db: Session = Depends(get_db)
):
    results = []
    success_count = 0
    fail_count = 0

    for item in req.items:
        c_type = (item.content_type or "movie").lower().strip()
        try:
            if c_type in ("web_series", "tv", "series"):
                series = ingest_series_by_tmdb_id(db, item.tmdb_id)
                results.append(BulkIngestTmdbResultItem(
                    tmdb_id=item.tmdb_id,
                    content_type="web_series",
                    status="success",
                    message=f"TV Series '{series.title}' ({series.total_seasons} seasons) successfully ingested!",
                    content_id=series.series_id,
                    title=series.title,
                    poster_image_url=series.poster_image_url
                ))
            else:
                movie = ingest_movie_by_tmdb_id(db, item.tmdb_id)
                results.append(BulkIngestTmdbResultItem(
                    tmdb_id=item.tmdb_id,
                    content_type="movie",
                    status="success",
                    message=f"Movie '{movie.title}' successfully ingested!",
                    content_id=movie.id,
                    title=movie.title,
                    poster_image_url=movie.poster_image_url
                ))
            success_count += 1
        except Exception as e:
            logger.error(f"Bulk ingestion error for TMDB ID {item.tmdb_id}: {e}")
            results.append(BulkIngestTmdbResultItem(
                tmdb_id=item.tmdb_id,
                content_type=c_type,
                status="error",
                message=str(e)
            ))
            fail_count += 1

    return BulkIngestTmdbResponse(
        total_requested=len(req.items),
        successful_count=success_count,
        failed_count=fail_count,
        results=results
    )


@router.put("/universes/{universe_id}", response_model=CinematicUniverseResponse, summary="Update Universe Name and Description (Admin/Owner only)")
def update_cinematic_universe(
    universe_id: int,
    req: UpdateCinematicUniverseRequest,
    current_user: User = Depends(get_admin_or_owner_user),
    db: Session = Depends(get_db)
):
    universe = db.query(CinematicUniverse).options(joinedload(CinematicUniverse.contents)).filter(CinematicUniverse.id == universe_id).first()
    if not universe:
        raise HTTPException(status_code=404, detail="Cinematic Universe not found")

    if req.name is not None and req.name.strip():
        name_clean = req.name.strip()
        existing = db.query(CinematicUniverse).filter(
            CinematicUniverse.name.ilike(name_clean),
            CinematicUniverse.id != universe_id
        ).first()
        if existing:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Another Cinematic Universe already has this name.")
        universe.name = name_clean

    if req.description is not None:
        universe.description = req.description.strip() if req.description else None

    db.commit()
    universe = db.query(CinematicUniverse).options(joinedload(CinematicUniverse.contents)).filter(CinematicUniverse.id == universe_id).first()
    return build_universe_response(universe, db)


@router.put("/universes/{universe_id}/contents/reorder", response_model=CinematicUniverseResponse, summary="Reorder Contents in Cinematic Universe (Admin/Owner only)")
def reorder_universe_contents(
    universe_id: int,
    req: ReorderUniverseContentsRequest,
    current_user: User = Depends(get_admin_or_owner_user),
    db: Session = Depends(get_db)
):
    universe = db.query(CinematicUniverse).options(joinedload(CinematicUniverse.contents)).filter(CinematicUniverse.id == universe_id).first()
    if not universe:
        raise HTTPException(status_code=404, detail="Cinematic Universe not found")

    content_map = {c.content_id: c for c in universe.contents}
    for idx, cid in enumerate(req.content_ids):
        item = content_map.get(cid)
        if item:
            item.order_in_universe = idx + 1

    db.commit()
    universe = db.query(CinematicUniverse).options(joinedload(CinematicUniverse.contents)).filter(CinematicUniverse.id == universe_id).first()
    return build_universe_response(universe, db)


@router.patch("/{content_id}/ott-url", response_model=MovieResponse, summary="Set or Update OTT streaming URL for Movie or Series (Admin/Owner only)")
def update_content_ott_url(
    content_id: str,
    req: UpdateOttUrlRequest,
    current_user: User = Depends(get_admin_or_owner_user),
    db: Session = Depends(get_db)
):
    """
    Sets or updates the direct OTT streaming URL (e.g. Netflix, Prime Video, Disney+)
    and platform name for a movie or TV show.
    """
    clean_id = content_id.strip()
    clean_url = req.ott_url.strip() if req.ott_url else None
    clean_platform = req.ott_platform.strip() if req.ott_platform else None

    # Check Movie first
    movie = db.query(Movie).options(joinedload(Movie.director)).filter(Movie.id == clean_id).first()
    if movie:
        movie.ott_url = clean_url
        movie.ott_platform = clean_platform
        db.commit()
        db.refresh(movie)
        return movie_to_movie_response(movie)

    # Check WebSeries
    series = db.query(WebSeries).options(joinedload(WebSeries.director)).filter(WebSeries.series_id == clean_id).first()
    if series:
        series.ott_url = clean_url
        series.ott_platform = clean_platform
        db.commit()
        db.refresh(series)
        return series_to_movie_response(series)

    raise HTTPException(status_code=404, detail=f"Title with ID '{clean_id}' not found.")


@router.get("/{content_id}/ott-url", summary="Get OTT streaming URL and platform for a Movie or Series")
def get_content_ott_url(
    content_id: str,
    db: Session = Depends(get_db)
):
    clean_id = content_id.strip()
    movie = db.query(Movie).filter(Movie.id == clean_id).first()
    if movie:
        return {
            "content_id": movie.id,
            "title": movie.title,
            "content_type": "movie",
            "ott_url": movie.ott_url,
            "ott_platform": movie.ott_platform
        }

    series = db.query(WebSeries).filter(WebSeries.series_id == clean_id).first()
    if series:
        return {
            "content_id": series.series_id,
            "title": series.title,
            "content_type": "web_series",
            "ott_url": series.ott_url,
            "ott_platform": series.ott_platform
        }

    raise HTTPException(status_code=404, detail=f"Title with ID '{clean_id}' not found.")


@router.get("/{movie_id}", response_model=MovieResponse, summary="Get Single Movie Details")
def get_movie_detail(movie_id: str, db: Session = Depends(get_db)):
    movie = db.query(Movie).options(joinedload(Movie.director)).filter(Movie.id == movie_id).first()
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")
    return movie_to_movie_response(movie)



