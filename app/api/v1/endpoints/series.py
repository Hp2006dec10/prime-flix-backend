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

import httpx
from app.core.config import settings

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

    if series.tmdb_id and (series.imdb_id is None or series.director_id is None):
        try:
            url = f"https://api.themoviedb.org/3/tv/{series.tmdb_id}"
            params = {"api_key": settings.TMDB_API_KEY, "append_to_response": "credits,external_ids"}
            resp = httpx.get(url, params=params, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                updated = False
                if series.imdb_id is None:
                    ext_ids = data.get("external_ids", {}) or {}
                    imdb_id = ext_ids.get("imdb_id")
                    if imdb_id:
                        series.imdb_id = str(imdb_id).strip()
                        updated = True
                if series.director_id is None:
                    creator = None
                    created_by = data.get("created_by", [])
                    if created_by and len(created_by) > 0 and created_by[0].get("name"):
                        creator = created_by[0]["name"].strip()
                    else:
                        credits_data = data.get("credits", {}) or {}
                        crew = credits_data.get("crew", []) or []
                        dir_entry = next((c for c in crew if c.get("job") == "Director" or c.get("department") == "Directing"), None)
                        if dir_entry and dir_entry.get("name"):
                            creator = dir_entry["name"].strip()
                    if creator:
                        d = db.query(Director).filter(Director.name.ilike(creator)).first()
                        if not d:
                            d = Director(name=creator)
                            db.add(d)
                            db.flush()
                        series.director_id = d.id
                        series.director = d
                        updated = True
                if updated:
                    db.commit()
                    db.refresh(series)
        except Exception as e:
            logger.warning(f"Failed to auto-enrich series {series.series_id}: {e}")

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
