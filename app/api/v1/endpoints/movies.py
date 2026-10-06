import json
import logging
import random
import httpx
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.db.session import get_db
from app.db.models import Movie, Director
from app.schemas.movies import (
    MovieResponse,
    MoodRequest,
    MoodResponse,
    MovieCategorySection
)
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_model=List[MovieResponse], summary="Get All Movies")
def get_all_movies(db: Session = Depends(get_db)):
    movies = db.query(Movie).options(joinedload(Movie.director)).order_by(Movie.id.desc()).all()
    return movies


from typing import List, Optional
from app.schemas.movies import (
    MovieResponse,
    MoodRequest,
    MoodResponse,
    MovieCategorySection,
    PaginatedCategoryMovies,
    FilterOptionsResponse
)


@router.get("/filter-options", response_model=FilterOptionsResponse, summary="Get Filter Options for Genre, Language & Directors")
def get_filter_options(db: Session = Depends(get_db)):
    movies = db.query(Movie).options(joinedload(Movie.director)).all()
    
    genres_set = set()
    languages_set = set()
    
    for m in movies:
        if m.genre:
            for g in m.genre.split(","):
                g_clean = g.strip()
                if g_clean:
                    genres_set.add(g_clean)
        if m.language:
            for l in m.language.split(","):
                l_clean = l.strip()
                if l_clean:
                    languages_set.add(l_clean)
                    
    directors = db.query(Director).order_by(Director.name).all()
    
    return FilterOptionsResponse(
        genres=sorted(list(genres_set)),
        languages=sorted(list(languages_set)),
        directors=directors
    )


@router.get("/search", response_model=List[MovieResponse], summary="Search and Filter Movies by Keyword, Genre, Language, and Director")
def search_movies(
    query: Optional[str] = None,
    genre: Optional[str] = None,
    language: Optional[str] = None,
    director_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    q = db.query(Movie).options(joinedload(Movie.director))
    
    if director_id and director_id > 0:
        q = q.filter(Movie.director_id == director_id)
        
    if genre and genre.upper() != "ALL":
        q = q.filter(Movie.genre.ilike(f"%{genre}%"))
        
    if language and language.upper() != "ALL":
        q = q.filter(Movie.language.ilike(f"%{language}%"))
        
    movies = q.order_by(Movie.id.desc()).all()
    
    if query and query.strip():
        search_lower = query.strip().lower()
        matched = []
        for m in movies:
            title_match = search_lower in (m.title or "").lower()
            desc_match = search_lower in (m.description or "").lower()
            director_match = m.director and search_lower in (m.director.name or "").lower()
            genre_match = search_lower in (m.genre or "").lower()
            if title_match or desc_match or director_match or genre_match:
                matched.append(m)
        movies = matched

    return movies


def filter_category_list(movies: List[Movie], category_type: str, director_name: Optional[str] = None) -> List[Movie]:
    if category_type == "top_rated":
        # Pause fetching at 9.5 rating (only include movies with rating >= 9.5)
        top = [m for m in movies if m.average_rating is not None and m.average_rating >= 9.5]
        return sorted(top, key=lambda x: x.average_rating or 0, reverse=True)
    elif category_type == "director" and director_name:
        return [m for m in movies if m.director and m.director.name == director_name]
    elif category_type == "genre":
        return [m for m in movies if any(w in (m.description or "").lower() for w in ["mind", "thriller", "crime", "fight", "dark", "secret", "mystery"])]
    elif category_type == "universe":
        return [m for m in movies if (m.release_year and m.release_year < 2010)]
    else:  # 'all'
        return sorted(movies, key=lambda x: x.id, reverse=True)


@router.get("/categories", response_model=List[MovieCategorySection], summary="Get Categorized Movie Collections")
def get_movie_categories(db: Session = Depends(get_db)):
    movies = db.query(Movie).options(joinedload(Movie.director)).all()
    if not movies:
        return []

    sections = []

    # 1. Top Rated Movies
    top_rated_full = filter_category_list(movies, "top_rated")
    if top_rated_full:
        sections.append(
            MovieCategorySection(
                title="⭐ Top Rated Masterpieces",
                description="Highest user rated critically acclaimed films",
                category_type="top_rated",
                has_more=len(top_rated_full) > 5,
                movies=top_rated_full[:5]
            )
        )

    # 2. Director Spotlight
    directors_map = {}
    for m in movies:
        if m.director:
            directors_map.setdefault(m.director.name, []).append(m)
    
    if directors_map:
        spotlight_director, director_movies = random.choice(list(directors_map.items()))
        sections.append(
            MovieCategorySection(
                title=f"🎬 Director Spotlight: {spotlight_director}",
                description=f"Films directed by {spotlight_director}",
                category_type="director",
                director_name=spotlight_director,
                has_more=len(director_movies) > 5,
                movies=director_movies[:5]
            )
        )

    # 3. Mind-Bending Thrillers
    thrillers_full = filter_category_list(movies, "genre")
    if thrillers_full:
        sections.append(
            MovieCategorySection(
                title="🧠 Mind-Bending & Psychological Thrillers",
                description="Intense stories filled with twists, secrets, and suspense",
                category_type="genre",
                has_more=len(thrillers_full) > 5,
                movies=thrillers_full[:5]
            )
        )

    # 4. Cinematic Classics
    classics_full = filter_category_list(movies, "universe")
    if classics_full:
        sections.append(
            MovieCategorySection(
                title="🏆 Timeless Cinematic Classics",
                description="Unforgettable stories that defined generations",
                category_type="universe",
                has_more=len(classics_full) > 5,
                movies=classics_full[:5]
            )
        )

    # 5. Full Collection
    all_full = filter_category_list(movies, "all")
    sections.append(
        MovieCategorySection(
            title="🍿 Full PrimeFlix Collection",
            description="Explore our complete database of high definition movies",
            category_type="all",
            has_more=len(all_full) > 5,
            movies=all_full[:5]
        )
    )

    return sections


@router.get("/category-movies", response_model=PaginatedCategoryMovies, summary="Fetch Paginated Category Movies on Horizontal Scroll")
def get_category_movies(
    category_type: str,
    offset: int = 0,
    limit: int = 5,
    director_name: Optional[str] = None,
    db: Session = Depends(get_db)
):
    all_movies = db.query(Movie).options(joinedload(Movie.director)).all()
    filtered = filter_category_list(all_movies, category_type=category_type, director_name=director_name)
    
    paginated_movies = filtered[offset : offset + limit]
    has_more = (offset + limit) < len(filtered)

    return PaginatedCategoryMovies(
        category_type=category_type,
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

    movies = db.query(Movie).options(joinedload(Movie.director)).all()
    if not movies:
        raise HTTPException(status_code=404, detail="No movies available in database")

    # Format compact catalog representation for LLM context
    catalog = [
        {
            "id": m.id,
            "title": m.title,
            "year": m.release_year,
            "rating": m.average_rating,
            "director": m.director.name if m.director else None,
            "description": m.description[:200] if m.description else ""
        }
        for m in movies
    ]

    system_prompt = (
        "You are an expert AI movie recommendation engine for PrimeFlix. "
        "Analyze the user's current mood or situation description and pick the best matching movie IDs from the provided movie catalog.\n\n"
        "Movie Catalog:\n"
        f"{json.dumps(catalog, indent=2)}\n\n"
        "INSTRUCTIONS:\n"
        "1. Select between 1 to 5 movie IDs from the catalog above that best match the user's mood.\n"
        "2. Provide a short, captivating 2-sentence explanation of why these movies fit the mood.\n"
        "3. You MUST respond with ONLY a valid JSON object in the following format:\n"
        '{\n  "explanation": "Your explanation here...",\n  "recommended_ids": [1, 2]\n}'
    )

    user_prompt = f"User Mood / Query: {mood}"

    recommended_movies = []
    explanation = "Here are the top movie recommendations tailored for your mood."

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
                        
                        id_map = {m.id: m for m in movies}
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
        for m in movies:
            text = f"{m.title} {m.description or ''}".lower()
            if any(w in text for w in words if len(w) > 3):
                matched.append(m)
        recommended_movies = matched[:4] if matched else movies[:4]
        explanation = f"Based on your mood query '{mood}', here are movies you might enjoy!"

    return MoodResponse(
        mood=mood,
        explanation=explanation,
        movies=recommended_movies
    )


@router.get("/{movie_id}", response_model=MovieResponse, summary="Get Single Movie Details")
def get_movie_detail(movie_id: int, db: Session = Depends(get_db)):
    movie = db.query(Movie).options(joinedload(Movie.director)).filter(Movie.id == movie_id).first()
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")
    return movie
