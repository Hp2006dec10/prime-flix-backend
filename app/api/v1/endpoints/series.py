import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload, selectinload

from app.db.session import get_db
from app.db.models import WebSeries, Season, Episode, Director
from app.schemas.series import (
    WebSeriesResponse,
    WebSeriesDetailResponse,
    SeasonResponse,
    SeasonSummaryResponse,
    EpisodeResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_model=List[WebSeriesResponse], summary="Get All Web Series / TV Shows")
def get_all_series(
    query: Optional[str] = Query(None, description="Search by title or description"),
    genre: Optional[str] = Query(None, description="Filter by genre"),
    language: Optional[str] = Query(None, description="Filter by language"),
    year: Optional[int] = Query(None, description="Filter by release year"),
    director_id: Optional[int] = Query(None, description="Filter by director ID"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    q = db.query(WebSeries).options(joinedload(WebSeries.director))

    if query and query.strip():
        term = f"%{query.strip().lower()}%"
        q = q.filter(
            or_(
                WebSeries.title.ilike(term),
                WebSeries.description.ilike(term),
            )
        )

    if genre and genre.strip() and genre.upper() != "ALL":
        q = q.filter(WebSeries.genre.ilike(f"%{genre.strip()}%"))

    if language and language.strip() and language.upper() != "ALL":
        q = q.filter(WebSeries.language.ilike(f"%{language.strip()}%"))

    if year and year > 0:
        q = q.filter(WebSeries.release_year == year)

    if director_id and director_id > 0:
        q = q.filter(WebSeries.director_id == director_id)

    series_list = (
        q.order_by(WebSeries.average_rating.desc().nullslast(), WebSeries.release_year.desc().nullslast())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return series_list


@router.get("/{series_id}", response_model=WebSeriesDetailResponse, summary="Get TV Show / Web Series Details with Seasons & Episodes")
def get_series_detail(series_id: str, db: Session = Depends(get_db)):
    series = (
        db.query(WebSeries)
        .options(
            joinedload(WebSeries.director),
            selectinload(WebSeries.seasons).selectinload(Season.episodes),
        )
        .filter(WebSeries.series_id == series_id)
        .first()
    )

    if not series:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Web Series with ID '{series_id}' not found",
        )

    return series


@router.get("/{series_id}/seasons", response_model=List[SeasonSummaryResponse], summary="Get All Seasons for a Series")
def get_series_seasons(series_id: str, db: Session = Depends(get_db)):
    series = db.query(WebSeries).filter(WebSeries.series_id == series_id).first()
    if not series:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Web Series with ID '{series_id}' not found",
        )

    seasons = (
        db.query(Season)
        .filter(Season.series_id == series_id)
        .order_by(Season.season_number.asc())
        .all()
    )
    return seasons


@router.get("/{series_id}/seasons/{season_number}", response_model=SeasonResponse, summary="Get Season Details with All Episodes")
def get_season_detail(series_id: str, season_number: int, db: Session = Depends(get_db)):
    season = (
        db.query(Season)
        .options(selectinload(Season.episodes))
        .filter(Season.series_id == series_id, Season.season_number == season_number)
        .first()
    )

    if not season:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Season {season_number} for series '{series_id}' not found",
        )

    return season


@router.get("/{series_id}/seasons/{season_number}/episodes/{episode_number}", response_model=EpisodeResponse, summary="Get Episode Details")
def get_episode_detail(
    series_id: str,
    season_number: int,
    episode_number: int,
    db: Session = Depends(get_db),
):
    season = (
        db.query(Season)
        .filter(Season.series_id == series_id, Season.season_number == season_number)
        .first()
    )
    if not season:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Season {season_number} for series '{series_id}' not found",
        )

    episode = (
        db.query(Episode)
        .filter(Episode.season_id == season.season_id, Episode.episode_number == episode_number)
        .first()
    )
    if not episode:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Episode {episode_number} of Season {season_number} not found",
        )

    return episode
