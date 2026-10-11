from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from app.schemas.movies import MovieResponse


class WatchlistCreateRequest(BaseModel):
    content_id: str
    content_type: Optional[str] = "movie"
    status: Optional[str] = "to_watch"  # 'watching', 'completed', 'to_watch', 'dropped'
    rating: Optional[float] = None
    notes: Optional[str] = None


class WatchlistUpdateRequest(BaseModel):
    status: str  # 'watching', 'completed', 'to_watch', 'dropped'
    rating: Optional[float] = None
    notes: Optional[str] = None


class WatchlistItemResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    content_id: str
    content_type: str
    status: str
    completed_at: Optional[datetime] = None
    rating: Optional[float] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    movie: Optional[MovieResponse] = None

    class Config:
        from_attributes = True


class WatchlistStatsResponse(BaseModel):
    total: int
    watching: int
    completed: int
    to_watch: int
    dropped: int
