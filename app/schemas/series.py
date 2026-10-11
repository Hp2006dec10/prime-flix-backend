from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from app.schemas.movies import DirectorResponse


class EpisodeResponse(BaseModel):
    episode_id: str
    season_id: str
    episode_number: int
    title: str
    duration: Optional[int] = None
    description: Optional[str] = None
    still_image_url: Optional[str] = None
    average_rating: Optional[float] = None
    release_date: Optional[str] = None
    tmdb_id: Optional[int] = None

    class Config:
        from_attributes = True


class SeasonResponse(BaseModel):
    season_id: str
    series_id: str
    season_number: int
    release_year: Optional[int] = None
    title: Optional[str] = None
    description: Optional[str] = None
    poster_image_url: Optional[str] = None
    episode_count: Optional[int] = None
    tmdb_id: Optional[int] = None
    episodes: List[EpisodeResponse] = []

    class Config:
        from_attributes = True


class SeasonSummaryResponse(BaseModel):
    season_id: str
    series_id: str
    season_number: int
    release_year: Optional[int] = None
    title: Optional[str] = None
    description: Optional[str] = None
    poster_image_url: Optional[str] = None
    episode_count: Optional[int] = None

    class Config:
        from_attributes = True


class WebSeriesResponse(BaseModel):
    series_id: str
    tmdb_id: Optional[int] = None
    imdb_id: Optional[str] = None
    title: str
    average_rating: Optional[float] = None
    description: Optional[str] = None
    release_year: Optional[int] = None
    total_seasons: int = 1
    poster_image_url: Optional[str] = None
    genre: Optional[str] = None
    genre_percentages: Optional[Dict[str, int]] = None
    language: Optional[str] = None
    director_id: Optional[int] = None
    director: Optional[DirectorResponse] = None
    ott_url: Optional[str] = None
    ott_platform: Optional[str] = None
    ott_providers: Optional[List[Dict[str, Any]]] = None

    class Config:
        from_attributes = True


class WebSeriesDetailResponse(WebSeriesResponse):
    seasons: List[SeasonResponse] = []

    class Config:
        from_attributes = True
