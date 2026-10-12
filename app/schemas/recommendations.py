from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from app.schemas.movies import MovieResponse

class RecommendationRequest(BaseModel):
    mood: Optional[str] = Field(None, description="User's input mood, vibe, or occasion")
    genres: Optional[List[str]] = Field(None, description="Preferred genres")
    content_type: Optional[str] = Field("all", description="'all', 'movie', or 'web_series'")
    include_prequels_sequels: Optional[bool] = Field(True, description="Whether to include prequel/sequel suggestions based on history and watchlist")
    limit: Optional[int] = Field(12, description="Maximum number of recommendations to return")

class RecommendedItem(BaseModel):
    content: MovieResponse
    recommendation_reason: str
    recommendation_type: str = Field(
        ...,
        description="'sequel' | 'prequel' | 'universe_next' | 'mood_match' | 'genre_taste' | 'watchlist_companion' | 'history_inspired'"
    )
    related_title: Optional[str] = None
    match_score: int = Field(..., description="Calculated recommendation match score from 0 to 100")
    genre_match_breakdown: Optional[Dict[str, int]] = None

class UserProfileSummary(BaseModel):
    top_genres: Dict[str, int] = Field(default_factory=dict, description="Genre percentage distribution from user activity")
    history_count: int = 0
    watchlist_count: int = 0
    reviews_count: int = 0
    favorite_universes: List[str] = Field(default_factory=list)

class RecommendationResponse(BaseModel):
    query_mood: Optional[str] = None
    query_genres: Optional[List[str]] = None
    ai_explanation: str
    personalized_for_you: List[RecommendedItem] = Field(default_factory=list)
    sequels_and_prequels: List[RecommendedItem] = Field(default_factory=list)
    mood_and_genre_picks: List[RecommendedItem] = Field(default_factory=list)
    history_inspired: List[RecommendedItem] = Field(default_factory=list)
    user_profile: Optional[UserProfileSummary] = None
