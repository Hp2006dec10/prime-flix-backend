import logging
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_optional_current_user, get_current_user
from app.db.session import get_db
from app.db.models import User
from app.schemas.recommendations import (
    RecommendationRequest,
    RecommendationResponse,
    RecommendedItem,
    UserProfileSummary
)
from app.services.recommendation_service import (
    get_ai_recommendations,
    compute_user_profile,
    find_prequels_and_sequels
)
from app.db.models import Movie, WebSeries
from app.api.v1.endpoints.movies import movie_to_movie_response, series_to_movie_response

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/ai-suggestions", response_model=RecommendationResponse, summary="Get AI Recommendations based on Mood, Genres, Watchlist, History, and Sequels/Prequels")
def get_recommendations_endpoint(
    request: RecommendationRequest,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db)
):
    try:
        return get_ai_recommendations(request, current_user, db)
    except Exception as e:
        logger.error(f"Error computing AI recommendations: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate AI recommendations")

@router.get("/my-taste", response_model=UserProfileSummary, summary="Get Current User's Learned Entertainment Profile & Genre Taste")
def get_user_taste_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    profile = compute_user_profile(current_user.id, db)
    return UserProfileSummary(
        top_genres=profile["top_genres"],
        history_count=profile["history_count"],
        watchlist_count=profile["watchlist_count"],
        reviews_count=profile["reviews_count"],
        favorite_universes=profile["favorite_universes"]
    )

@router.get("/franchise-continuations", response_model=List[RecommendedItem], summary="Get Prequels and Sequels for Titles in User's Watchlist & History")
def get_franchise_continuations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    profile = compute_user_profile(current_user.id, db)
    all_movies = db.query(Movie).all()
    all_series = db.query(WebSeries).all()

    movie_map = {m.id: movie_to_movie_response(m) for m in all_movies}
    for s in all_series:
        movie_map[s.series_id] = series_to_movie_response(s)

    raw_items = find_prequels_and_sequels(profile, all_movies, all_series, db)
    results = []
    for item in raw_items:
        cid = getattr(item["item"], "id", None) or getattr(item["item"], "series_id", None)
        if cid and cid in movie_map:
            resp = movie_map[cid]
            results.append(
                RecommendedItem(
                    content=resp,
                    recommendation_reason=item["reason"],
                    recommendation_type=item["type"],
                    related_title=item["related_title"],
                    match_score=item["score"],
                    genre_match_breakdown=resp.genre_percentages if isinstance(resp.genre_percentages, dict) else None
                )
            )
    return results
