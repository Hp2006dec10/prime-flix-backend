from pydantic import BaseModel
from typing import Optional, List


class DirectorResponse(BaseModel):
    id: int
    name: str
    nationality: Optional[str] = None

    class Config:
        from_attributes = True


class MovieResponse(BaseModel):
    id: int
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

    class Config:
        from_attributes = True


class MoodRequest(BaseModel):
    mood: str


class MoodResponse(BaseModel):
    mood: str
    explanation: str
    movies: List[MovieResponse]


class MovieCategorySection(BaseModel):
    title: str
    description: str
    category_type: str  # 'genre', 'universe', 'director', 'top_rated', 'all'
    director_name: Optional[str] = None
    has_more: bool = False
    movies: List[MovieResponse]


class PaginatedCategoryMovies(BaseModel):
    category_type: str
    offset: int
    limit: int
    has_more: bool
    movies: List[MovieResponse]


class FilterOptionsResponse(BaseModel):
    genres: List[str]
    languages: List[str]
    directors: List[DirectorResponse]


