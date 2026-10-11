import logging
from typing import List, Optional, Dict
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc

from app.db.session import get_db
from app.db.models import Review, User, Movie, WebSeries
from app.api.deps import get_current_user, get_optional_current_user
from app.schemas.reviews import (
    ReviewCreateRequest,
    ReviewResponse,
    ContentReviewsSummaryResponse,
    UserBasicResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def build_review_response(review: Review, db: Session) -> ReviewResponse:
    # Resolve content title and poster
    movie_title = None
    poster_image_url = None

    if review.content_id.startswith("SER-") or review.content_type == "web_series":
        series = db.query(WebSeries).filter(WebSeries.series_id == review.content_id).first()
        if series:
            movie_title = series.title
            poster_image_url = series.poster_image_url
    else:
        movie = db.query(Movie).filter(Movie.id == review.content_id).first()
        if movie:
            movie_title = movie.title
            poster_image_url = movie.poster_image_url

    user_info = None
    if review.user:
        user_info = UserBasicResponse(
            id=review.user.id,
            full_name=review.user.full_name,
            email=review.user.email
        )

    return ReviewResponse(
        id=review.id,
        user_id=review.user_id,
        content_id=review.content_id,
        content_type=review.content_type,
        rating=review.rating,
        review_title=review.review_title,
        review_text=review.review_text,
        user_genre_percentages=review.user_genre_percentages,
        created_at=review.created_at,
        updated_at=review.updated_at,
        user=user_info,
        movie_title=movie_title,
        poster_image_url=poster_image_url
    )


def calculate_community_genres(reviews: List[Review]) -> Optional[Dict[str, int]]:
    """
    Aggregates user-submitted genre percentage opinions and calculates
    consensus community genre percentages summing to 100%.
    """
    genre_totals: Dict[str, float] = {}
    valid_count = 0

    for r in reviews:
        if r.user_genre_percentages and isinstance(r.user_genre_percentages, dict):
            valid_count += 1
            for g, pct in r.user_genre_percentages.items():
                genre_totals[g] = genre_totals.get(g, 0.0) + float(pct)

    if not genre_totals or valid_count == 0:
        return None

    # Calculate average per genre
    avg_scores = {g: total / valid_count for g, total in genre_totals.items()}
    # Pick top 3-4 genres
    sorted_genres = sorted(avg_scores.items(), key=lambda x: x[1], reverse=True)[:4]
    sum_scores = sum(score for _, score in sorted_genres)

    if sum_scores <= 0:
        return None

    # Normalize to exact 100
    res = {}
    cur_sum = 0
    for i, (g, score) in enumerate(sorted_genres):
        if i == len(sorted_genres) - 1:
            val = max(5, 100 - cur_sum)
            res[g] = val
        else:
            val = max(5, int(round((score / sum_scores) * 100)))
            res[g] = val
            cur_sum += val

    diff = 100 - sum(res.values())
    if diff != 0:
        top_k = sorted_genres[0][0]
        res[top_k] += diff

    return dict(sorted(res.items(), key=lambda x: x[1], reverse=True))


@router.get("/content/{content_id}", response_model=ContentReviewsSummaryResponse, summary="Get Reviews and Community Consensus for a Title")
def get_content_reviews(content_id: str, db: Session = Depends(get_db)):
    cid = content_id.strip()
    reviews = (
        db.query(Review)
        .options(joinedload(Review.user))
        .filter(Review.content_id == cid)
        .order_by(desc(Review.created_at))
        .all()
    )

    avg_rating = None
    if reviews:
        avg_rating = round(sum(r.rating for r in reviews) / len(reviews), 1)

    community_genres = calculate_community_genres(reviews)

    review_responses = [build_review_response(r, db) for r in reviews]

    return ContentReviewsSummaryResponse(
        content_id=cid,
        average_user_rating=avg_rating,
        total_reviews=len(reviews),
        community_genre_percentages=community_genres,
        reviews=review_responses
    )


@router.post("", response_model=ReviewResponse, summary="Submit or Update User Review, Rating, and Genre Opinion")
def submit_review(
    payload: ReviewCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    cid = payload.content_id.strip()
    # Check if content exists
    is_series = cid.startswith("SER-") or payload.content_type == "web_series"
    if is_series:
        series = db.query(WebSeries).filter(WebSeries.series_id == cid).first()
        if not series:
            raise HTTPException(status_code=404, detail="Web Series not found")
        actual_type = "web_series"
    else:
        movie = db.query(Movie).filter(Movie.id == cid).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Movie not found")
        actual_type = "movie"

    # Normalize user genre percentages if provided
    cleaned_percentages = None
    if payload.user_genre_percentages and isinstance(payload.user_genre_percentages, dict):
        raw = {k.strip(): int(v) for k, v in payload.user_genre_percentages.items() if int(v) > 0}
        total = sum(raw.values())
        if total > 0:
            if total != 100:
                diff = 100 - total
                top_k = max(raw, key=raw.get)
                raw[top_k] += diff
            cleaned_percentages = dict(sorted(raw.items(), key=lambda x: x[1], reverse=True))

    # Check if user already reviewed this content
    existing = db.query(Review).filter(
        Review.user_id == current_user.id,
        Review.content_id == cid
    ).first()

    if existing:
        existing.rating = payload.rating
        existing.review_title = payload.review_title.strip() if payload.review_title else None
        existing.review_text = payload.review_text.strip() if payload.review_text else None
        if cleaned_percentages is not None:
            existing.user_genre_percentages = cleaned_percentages
        review = existing
    else:
        review = Review(
            user_id=current_user.id,
            content_id=cid,
            content_type=actual_type,
            rating=payload.rating,
            review_title=payload.review_title.strip() if payload.review_title else None,
            review_text=payload.review_text.strip() if payload.review_text else None,
            user_genre_percentages=cleaned_percentages
        )
        db.add(review)

    db.commit()
    db.refresh(review)
    return build_review_response(review, db)


@router.get("/my", response_model=List[ReviewResponse], summary="Get Current User's Reviews & Opinion Genres")
def get_my_reviews(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    reviews = (
        db.query(Review)
        .options(joinedload(Review.user))
        .filter(Review.user_id == current_user.id)
        .order_by(desc(Review.updated_at))
        .all()
    )
    return [build_review_response(r, db) for r in reviews]


@router.get("/recent", response_model=List[ReviewResponse], summary="Get Community Reviews Feed")
def get_recent_reviews(
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    reviews = (
        db.query(Review)
        .options(joinedload(Review.user))
        .order_by(desc(Review.created_at))
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [build_review_response(r, db) for r in reviews]


@router.delete("/{review_id}", summary="Delete User Review")
def delete_review(
    review_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    review = db.query(Review).filter(Review.id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    if review.user_id != current_user.id and current_user.role not in ("admin", "owner"):
        raise HTTPException(status_code=403, detail="Not authorized to delete this review")

    db.delete(review)
    db.commit()
    return {"message": "Review deleted successfully"}
