from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime


class UserBasicResponse(BaseModel):
    id: int
    full_name: str
    email: str

    class Config:
        from_attributes = True


class ReviewCreateRequest(BaseModel):
    content_id: str
    content_type: Optional[str] = "movie"
    rating: float = Field(..., ge=1.0, le=10.0)
    review_title: Optional[str] = None
    review_text: Optional[str] = None
    user_genre_percentages: Optional[Dict[str, int]] = None


class ReviewResponse(BaseModel):
    id: int
    user_id: int
    content_id: str
    content_type: str
    rating: float
    review_title: Optional[str] = None
    review_text: Optional[str] = None
    user_genre_percentages: Optional[Dict[str, int]] = None
    created_at: datetime
    updated_at: datetime
    user: Optional[UserBasicResponse] = None
    movie_title: Optional[str] = None
    poster_image_url: Optional[str] = None

    class Config:
        from_attributes = True


class ContentReviewsSummaryResponse(BaseModel):
    content_id: str
    average_user_rating: Optional[float] = None
    total_reviews: int = 0
    community_genre_percentages: Optional[Dict[str, int]] = None
    reviews: List[ReviewResponse] = []
