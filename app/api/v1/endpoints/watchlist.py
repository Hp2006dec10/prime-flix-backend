import logging
from typing import Optional, List
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select, desc

from app.db.session import get_db
from app.db.models import WatchlistItem, Movie, WebSeries, User
from app.api.deps import get_optional_current_user
from app.schemas.watchlist import (
    WatchlistCreateRequest,
    WatchlistUpdateRequest,
    WatchlistItemResponse,
    WatchlistStatsResponse,
)
from app.schemas.movies import MovieResponse, DirectorResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def resolve_movie_response(content_id: str, db: Session) -> Optional[MovieResponse]:
    cid = content_id.strip()
    m = db.query(Movie).options(joinedload(Movie.director)).filter(Movie.id == cid).first()
    if m:
        dir_res = DirectorResponse.from_orm(m.director) if m.director else None
        return MovieResponse(
            id=m.id,
            tmdb_id=m.tmdb_id,
            imdb_id=m.imdb_id,
            title=m.title,
            average_rating=m.average_rating,
            description=m.description,
            duration=m.duration,
            release_year=m.release_year,
            poster_image_url=m.poster_image_url,
            genre=m.genre,
            genre_percentages=m.genre_percentages,
            language=m.language,
            director_id=m.director_id,
            director=dir_res,
            content_type="movie",
            total_seasons=None,
            ott_url=getattr(m, "ott_url", None),
            ott_platform=getattr(m, "ott_platform", None),
            ott_providers=getattr(m, "ott_providers", None),
        )

    s = db.query(WebSeries).options(joinedload(WebSeries.director)).filter(WebSeries.series_id == cid).first()
    if s:
        dir_res = DirectorResponse.from_orm(s.director) if s.director else None
        return MovieResponse(
            id=s.series_id,
            tmdb_id=s.tmdb_id,
            imdb_id=s.imdb_id,
            title=s.title,
            average_rating=s.average_rating,
            description=s.description,
            duration=None,
            release_year=s.release_year,
            poster_image_url=s.poster_image_url,
            genre=s.genre,
            genre_percentages=s.genre_percentages,
            language=s.language,
            director_id=s.director_id,
            director=dir_res,
            content_type="web_series",
            total_seasons=s.total_seasons,
            ott_url=getattr(s, "ott_url", None),
            ott_platform=getattr(s, "ott_platform", None),
            ott_providers=getattr(s, "ott_providers", None),
        )

    return None


def item_to_response(item: WatchlistItem, db: Session) -> WatchlistItemResponse:
    movie_res = resolve_movie_response(item.content_id, db)
    return WatchlistItemResponse(
        id=item.id,
        user_id=item.user_id,
        content_id=item.content_id,
        content_type=item.content_type,
        status=item.status,
        completed_at=item.completed_at,
        rating=item.rating,
        notes=item.notes,
        created_at=item.created_at,
        updated_at=item.updated_at,
        movie=movie_res,
    )


@router.get("", response_model=List[WatchlistItemResponse], summary="Get Watchlist Items")
def get_watchlist(
    status: Optional[str] = None,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(WatchlistItem)
    if current_user:
        query = query.filter(WatchlistItem.user_id == current_user.id)
    else:
        # For guest / demo requests, show global watchlist items or guest entries
        pass

    if status and status.lower() != "all":
        query = query.filter(WatchlistItem.status == status.lower())

    query = query.order_by(desc(WatchlistItem.updated_at))
    items = query.all()

    return [item_to_response(item, db) for item in items]


@router.post("", response_model=WatchlistItemResponse, summary="Add or Update Watchlist Item")
def add_to_watchlist(
    req: WatchlistCreateRequest,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db),
):
    valid_statuses = {"watching", "completed", "to_watch", "dropped"}
    target_status = (req.status or "to_watch").lower().strip()
    if target_status not in valid_statuses:
        target_status = "to_watch"

    user_id = current_user.id if current_user else None

    # Check if already in watchlist
    query = db.query(WatchlistItem).filter(WatchlistItem.content_id == req.content_id)
    if user_id:
        query = query.filter(WatchlistItem.user_id == user_id)
    existing = query.first()

    now = utcnow()
    if existing:
        existing.status = target_status
        if target_status == "completed" and not existing.completed_at:
            existing.completed_at = now
        elif target_status != "completed":
            existing.completed_at = None
        if req.rating is not None:
            existing.rating = req.rating
        if req.notes is not None:
            existing.notes = req.notes
        existing.updated_at = now
        db.commit()
        db.refresh(existing)
        return item_to_response(existing, db)

    item = WatchlistItem(
        user_id=user_id,
        content_id=req.content_id,
        content_type=req.content_type or "movie",
        status=target_status,
        completed_at=now if target_status == "completed" else None,
        rating=req.rating,
        notes=req.notes,
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item_to_response(item, db)


@router.patch("/{content_id}", response_model=WatchlistItemResponse, summary="Update Watchlist Item Status")
def update_watchlist_status(
    content_id: str,
    req: WatchlistUpdateRequest,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db),
):
    valid_statuses = {"watching", "completed", "to_watch", "dropped"}
    target_status = req.status.lower().strip()
    if target_status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status '{req.status}'. Must be one of {valid_statuses}")

    user_id = current_user.id if current_user else None
    query = db.query(WatchlistItem).filter(WatchlistItem.content_id == content_id)
    if user_id:
        query = query.filter(WatchlistItem.user_id == user_id)
    item = query.first()

    if not item:
        # Create it if it doesn't exist
        now = utcnow()
        item = WatchlistItem(
            user_id=user_id,
            content_id=content_id,
            content_type="web_series" if content_id.startswith("SER-") else "movie",
            status=target_status,
            completed_at=now if target_status == "completed" else None,
            rating=req.rating,
            notes=req.notes,
            created_at=now,
            updated_at=now,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item_to_response(item, db)

    now = utcnow()
    item.status = target_status
    if target_status == "completed":
        item.completed_at = now
    else:
        item.completed_at = None

    if req.rating is not None:
        item.rating = req.rating
    if req.notes is not None:
        item.notes = req.notes

    item.updated_at = now
    db.commit()
    db.refresh(item)
    return item_to_response(item, db)


@router.delete("/{content_id}", summary="Remove Item from Watchlist")
def remove_from_watchlist(
    content_id: str,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db),
):
    user_id = current_user.id if current_user else None
    query = db.query(WatchlistItem).filter(WatchlistItem.content_id == content_id)
    if user_id:
        query = query.filter(WatchlistItem.user_id == user_id)
    item = query.first()

    if not item:
        raise HTTPException(status_code=404, detail="Watchlist item not found")

    db.delete(item)
    db.commit()
    return {"message": "Item removed from watchlist"}


@router.get("/history", response_model=List[WatchlistItemResponse], summary="Get Watch History (Completed Titles)")
def get_watch_history(
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(WatchlistItem).filter(WatchlistItem.status == "completed")
    if current_user:
        query = query.filter(WatchlistItem.user_id == current_user.id)

    items = query.order_by(desc(WatchlistItem.completed_at), desc(WatchlistItem.updated_at)).all()
    return [item_to_response(item, db) for item in items]


@router.delete("/history/clear", summary="Clear Watch History")
def clear_watch_history(
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(WatchlistItem).filter(WatchlistItem.status == "completed")
    if current_user:
        query = query.filter(WatchlistItem.user_id == current_user.id)

    deleted_count = query.delete(synchronize_session=False)
    db.commit()
    return {"message": f"Cleared {deleted_count} completed titles from watch history"}


@router.get("/stats", response_model=WatchlistStatsResponse, summary="Get Watchlist & History Statistics")
def get_watchlist_stats(
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(WatchlistItem)
    if current_user:
        query = query.filter(WatchlistItem.user_id == current_user.id)

    all_items = query.all()
    total = len(all_items)
    watching = sum(1 for i in all_items if i.status == "watching")
    completed = sum(1 for i in all_items if i.status == "completed")
    to_watch = sum(1 for i in all_items if i.status == "to_watch")
    dropped = sum(1 for i in all_items if i.status == "dropped")

    return WatchlistStatsResponse(
        total=total,
        watching=watching,
        completed=completed,
        to_watch=to_watch,
        dropped=dropped,
    )
