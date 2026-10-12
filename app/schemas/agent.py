from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
from app.schemas.recommendations import RecommendedItem


class WatchProgressRequest(BaseModel):
    content_id: str = Field(..., description="Content ID, e.g. MOV-1 or SER-1396")
    content_type: str = Field("movie", description="'movie' or 'web_series'")
    episode_id: Optional[str] = Field(None, description="Episode ID if series")
    progress_seconds: int = Field(..., ge=0, description="Current playback timestamp in seconds")
    duration_seconds: Optional[int] = Field(None, ge=0, description="Total duration in seconds")
    completed: Optional[bool] = Field(None, description="Manually flag completed, otherwise computed from >= 85%")


class WatchProgressResponse(BaseModel):
    content_id: str
    content_type: str
    progress_seconds: int
    duration_seconds: Optional[int]
    completion_rate: float
    completed: bool
    watched_at: datetime


class UserInteractionRequest(BaseModel):
    content_id: str = Field(..., description="Target movie or series ID")
    content_type: str = Field("movie", description="'movie' or 'web_series'")
    interaction_type: str = Field(..., description="'like' | 'dislike' | 'favorite' | 'watchlist' | 'rating'")
    rating: Optional[float] = Field(None, ge=1.0, le=10.0, description="Rating from 1.0 to 10.0 if applicable")


class UserInteractionResponse(BaseModel):
    id: int
    content_id: str
    content_type: str
    interaction_type: str
    rating: Optional[float]
    created_at: datetime


class TasteProfileResponse(BaseModel):
    user_id: Optional[int]
    top_genres: Dict[str, int]
    top_directors: List[str]
    completed_count: int
    in_progress_count: int
    liked_count: int
    disliked_count: int
    watchlist_count: int
    total_watch_time_minutes: int
    recently_completed: List[Dict[str, Any]]
    in_progress: List[Dict[str, Any]]
    liked_titles: List[Dict[str, Any]]


class AgentChatRequest(BaseModel):
    message: str = Field(..., description="User prompt or natural language instruction")
    conversation_id: Optional[str] = Field(None, description="Thread / Conversation ID for multi-turn context")


class AgentActionResult(BaseModel):
    action: str = Field(..., description="Action taken, e.g. 'added_to_watchlist', 'removed_from_watchlist'")
    content_id: str
    title: str
    status: str = "success"
    detail: Optional[str] = None


class AgentChatResponse(BaseModel):
    conversation_id: str
    message: str
    recommended_items: List[RecommendedItem] = Field(default_factory=list)
    actions_taken: List[AgentActionResult] = Field(default_factory=list)
    suggested_replies: List[str] = Field(default_factory=list)


class RenameConversationRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120, description="New title for the conversation thread")

