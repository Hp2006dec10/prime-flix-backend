import json
import logging
import random
import httpx
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.db.session import get_db
from app.db.models import Movie, Director, CinematicUniverse, UniverseContent, User
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
    BulkAddUniverseContentRequest
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


@router.get("/filter-options", response_model=FilterOptionsResponse, summary="Get Filter Options for Genre, Language, Years & Directors")
def get_filter_options(db: Session = Depends(get_db)):
    movies = db.query(Movie).options(joinedload(Movie.director)).all()
    
    genres_set = set()
    languages_set = set()
    years_set = set()
    
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
        if m.release_year:
            years_set.add(m.release_year)
                    
    directors = db.query(Director).order_by(Director.name).all()
    
    return FilterOptionsResponse(
        genres=sorted(list(genres_set)),
        languages=sorted(list(languages_set)),
        years=sorted(list(years_set), reverse=True),
        directors=directors
    )


@router.get("/search", response_model=List[MovieResponse], summary="Search and Filter Movies by Keyword, Genre, Language, Year, and Director")
def search_movies(
    query: Optional[str] = None,
    genre: Optional[str] = None,
    language: Optional[str] = None,
    release_year: Optional[int] = None,
    director_id: Optional[int] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    db: Session = Depends(get_db)
):
    q = db.query(Movie).options(joinedload(Movie.director))
    
    if director_id and director_id > 0:
        q = q.filter(Movie.director_id == director_id)
        
    if genre and genre.upper() != "ALL":
        q = q.filter(Movie.genre.ilike(f"%{genre}%"))
        
    if language and language.upper() != "ALL":
        q = q.filter(Movie.language.ilike(f"%{language}%"))
        
    if release_year and release_year > 0:
        q = q.filter(Movie.release_year == release_year)

    if query and query.strip():
        search_lower = query.strip().lower()
        q = q.filter(
            or_(
                Movie.title.ilike(f"%{search_lower}%"),
                Movie.description.ilike(f"%{search_lower}%"),
                Movie.genre.ilike(f"%{search_lower}%")
            )
        )
        
    q = q.order_by(Movie.release_year.desc().nullslast(), Movie.id.desc())

    if offset is not None and offset >= 0:
        q = q.offset(offset)
    if limit is not None and limit > 0:
        q = q.limit(limit)

    movies = q.all()
    return movies


def filter_category_list(
    movies: List[Movie],
    category_type: str,
    director_name: Optional[str] = None,
    genre_name: Optional[str] = None,
    release_year: Optional[int] = None
) -> List[Movie]:
    if category_type == "top_rated":
        # Pause fetching at 9.5 rating (only include movies with rating >= 9.5)
        top = [m for m in movies if m.average_rating is not None and m.average_rating >= 9.5]
        return sorted(top, key=lambda x: x.average_rating or 0, reverse=True)
    elif category_type in ["genre", "random_genre"] and genre_name:
        return [m for m in movies if m.genre and genre_name.lower() in m.genre.lower()]
    elif category_type in ["year", "random_year"] and release_year:
        return [m for m in movies if m.release_year and int(m.release_year) == int(release_year)]
    elif category_type == "director" and director_name:
        return [m for m in movies if m.director and m.director.name == director_name]
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

    # 2. Random Genre Row
    all_genres = set()
    for m in movies:
        if m.genre:
            for g in m.genre.split(","):
                g_clean = g.strip()
                if g_clean:
                    all_genres.add(g_clean)
    if all_genres:
        selected_genre = random.choice(sorted(list(all_genres)))
        genre_movies = filter_category_list(movies, "random_genre", genre_name=selected_genre)
        if genre_movies:
            sections.append(
                MovieCategorySection(
                    title=f"🎬 Genre Spotlight: {selected_genre}",
                    description=f"Curated selection of top {selected_genre} movies in our collection",
                    category_type="random_genre",
                    genre_name=selected_genre,
                    has_more=len(genre_movies) > 5,
                    movies=genre_movies[:5]
                )
            )

    # 3. Random Year Row
    all_years = list(set([m.release_year for m in movies if m.release_year]))
    if all_years:
        selected_year = random.choice(sorted(all_years, reverse=True))
        year_movies = filter_category_list(movies, "random_year", release_year=selected_year)
        if year_movies:
            sections.append(
                MovieCategorySection(
                    title=f"📅 Time Capsule: Released in {selected_year}",
                    description=f"Explore movies released in the year {selected_year}",
                    category_type="random_year",
                    release_year=selected_year,
                    has_more=len(year_movies) > 5,
                    movies=year_movies[:5]
                )
            )

    # 4. Director Spotlight (if directors exist)
    directors = [m.director.name for m in movies if m.director and m.director.name]
    if directors:
        selected_director = random.choice(list(set(directors)))
        director_movies = filter_category_list(movies, "director", director_name=selected_director)
        if director_movies:
            sections.append(
                MovieCategorySection(
                    title=f"🎥 Director Spotlight: {selected_director}",
                    description=f"Masterpieces directed by {selected_director}",
                    category_type="director",
                    director_name=selected_director,
                    has_more=len(director_movies) > 5,
                    movies=director_movies[:5]
                )
            )

    # 5. Random Cinematic Universe Row
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


@router.get("/category-movies", response_model=PaginatedCategoryMovies, summary="Fetch Paginated Category Movies on Horizontal Scroll")
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
    else:
        all_movies = db.query(Movie).options(joinedload(Movie.director)).all()
        filtered = filter_category_list(
            all_movies,
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
        movies_map = {m.id: m for m in movies}

    content_responses = []
    for c in universe.contents:
        movie_obj = movies_map.get(c.content_id)
        content_responses.append(
            UniverseContentResponse(
                id=c.id,
                universe_id=c.universe_id,
                content_id=c.content_id,
                content_type=c.content_type,
                order_in_universe=c.order_in_universe,
                movie=MovieResponse.model_validate(movie_obj) if movie_obj else None
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

    if req.content_type == "movie" or req.content_id.startswith("MOV-"):
        movie = db.query(Movie).filter(Movie.id == req.content_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail=f"Movie with ID '{req.content_id}' not found.")

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
        content_type=req.content_type or "movie",
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

        movie = db.query(Movie).filter(Movie.id == content_id).first()
        if not movie:
            continue

        max_order += 1
        new_content = UniverseContent(
            universe_id=universe_id,
            content_id=content_id,
            content_type=req.content_type or "movie",
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


@router.get("/{movie_id}", response_model=MovieResponse, summary="Get Single Movie Details")
def get_movie_detail(movie_id: str, db: Session = Depends(get_db)):
    movie = db.query(Movie).options(joinedload(Movie.director)).filter(Movie.id == movie_id).first()
    if not movie:
        raise HTTPException(status_code=404, detail="Movie not found")
    return movie


