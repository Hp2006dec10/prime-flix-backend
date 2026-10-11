from pydantic import BaseModel
from typing import Optional, List


class DirectorResponse(BaseModel):
    id: int
    name: str
    nationality: Optional[str] = None

    class Config:
        from_attributes = True


class MovieResponse(BaseModel):
    id: str
    tmdb_id: Optional[int] = None
    imdb_id: Optional[str] = None
    title: str
    average_rating: Optional[float] = None
    description: Optional[str] = None
    duration: Optional[int] = None
    release_year: Optional[int] = None
    poster_image_url: Optional[str] = None
    genre: Optional[str] = None
    language: Optional[str] = None
    director_id: Optional[int] = None
    director: Optional[DirectorResponse] = None
    content_type: Optional[str] = "movie"
    total_seasons: Optional[int] = None
    ott_url: Optional[str] = None
    ott_platform: Optional[str] = None

    class Config:
        from_attributes = True


class MoodRequest(BaseModel):
    mood: str
    content_type: Optional[str] = None


class MoodResponse(BaseModel):
    mood: str
    explanation: str
    movies: List[MovieResponse]


class MovieCategorySection(BaseModel):
    title: str
    description: str
    category_type: str  # 'random_genre', 'random_year', 'director', 'universe', 'top_rated', 'all'
    genre_name: Optional[str] = None
    release_year: Optional[int] = None
    director_name: Optional[str] = None
    universe_name: Optional[str] = None
    universe_id: Optional[int] = None
    has_more: bool = False
    movies: List[MovieResponse]


class PaginatedCategoryMovies(BaseModel):
    category_type: str
    genre_name: Optional[str] = None
    release_year: Optional[int] = None
    director_name: Optional[str] = None
    universe_name: Optional[str] = None
    universe_id: Optional[int] = None
    offset: int
    limit: int
    has_more: bool
    movies: List[MovieResponse]


class FilterOptionsResponse(BaseModel):
    genres: List[str]
    languages: List[str]
    years: List[int] = []
    directors: List[DirectorResponse]


class UniverseContentResponse(BaseModel):
    id: int
    universe_id: int
    content_id: str
    content_type: str
    order_in_universe: Optional[int] = None
    movie: Optional[MovieResponse] = None

    class Config:
        from_attributes = True


class CinematicUniverseResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    contents: List[UniverseContentResponse] = []

    class Config:
        from_attributes = True


class CreateCinematicUniverseRequest(BaseModel):
    name: str
    description: Optional[str] = None


class AddUniverseContentRequest(BaseModel):
    content_id: str
    content_type: Optional[str] = "movie"
    order_in_universe: Optional[int] = None


class BulkAddUniverseContentRequest(BaseModel):
    content_ids: List[str]
    content_type: Optional[str] = "movie"


class UnifiedSearchResponse(BaseModel):
    movies: List[MovieResponse] = []
    series: List[MovieResponse] = []
    universes: List[CinematicUniverseResponse] = []
    total_count: int


class IngestTmdbRequest(BaseModel):
    tmdb_id: int
    content_type: str = "movie"  # "movie" or "web_series"


class IngestTmdbResponse(BaseModel):
    message: str
    content_id: str
    content_type: str
    title: str
    poster_image_url: Optional[str] = None
    movie: Optional[MovieResponse] = None


class IngestTmdbItem(BaseModel):
    tmdb_id: int
    content_type: str = "movie"  # "movie" or "web_series"


class BulkIngestTmdbRequest(BaseModel):
    items: List[IngestTmdbItem]


class BulkIngestTmdbResultItem(BaseModel):
    tmdb_id: int
    content_type: str
    status: str  # "success" or "error"
    message: str
    content_id: Optional[str] = None
    title: Optional[str] = None
    poster_image_url: Optional[str] = None


class BulkIngestTmdbResponse(BaseModel):
    total_requested: int
    successful_count: int
    failed_count: int
    results: List[BulkIngestTmdbResultItem]


class UpdateCinematicUniverseRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class ReorderUniverseContentsRequest(BaseModel):
    content_ids: List[str]


class UpdateOttUrlRequest(BaseModel):
    ott_url: Optional[str] = None
    ott_platform: Optional[str] = None