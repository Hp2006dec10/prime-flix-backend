from fastapi import APIRouter
from app.api.v1.endpoints import health, auth, movies, series, watchlist, reviews, recommendations

api_router = APIRouter()
api_router.include_router(health.router, tags=["Health"])
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(movies.router, prefix="/movies", tags=["Movies"])
api_router.include_router(series.router, prefix="/series", tags=["TV Shows & Web Series"])
api_router.include_router(watchlist.router, prefix="/watchlist", tags=["Watchlist & History"])
api_router.include_router(reviews.router, prefix="/reviews", tags=["Reviews & Ratings"])
api_router.include_router(recommendations.router, prefix="/recommendations", tags=["AI Recommendations"])


