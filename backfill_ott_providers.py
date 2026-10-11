import logging
import httpx
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.db.models import Movie, WebSeries
from app.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ott_backfill")

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w200"


def fetch_providers(client: httpx.Client, tmdb_id: int, is_tv: bool = False):
    endpoint = "tv" if is_tv else "movie"
    url = f"{TMDB_BASE_URL}/{endpoint}/{tmdb_id}/watch/providers"
    params = {"api_key": settings.TMDB_API_KEY}

    try:
        res = client.get(url, params=params, timeout=10.0)
        if res.status_code == 200:
            data = res.json().get("results", {})
            country_data = data.get("IN") or data.get("US") or (list(data.values())[0] if data else {})
            if country_data:
                watch_link = country_data.get("link")
                structured_providers = []
                seen_names = set()

                for item in country_data.get("flatrate", []):
                    name = item.get("provider_name")
                    if name and name not in seen_names:
                        seen_names.add(name)
                        logo = item.get("logo_path")
                        structured_providers.append({
                            "name": name,
                            "type": "Stream (Included)",
                            "logo_url": f"{TMDB_IMAGE_BASE}{logo}" if logo else None,
                            "watch_url": watch_link
                        })

                for item in country_data.get("buy", []):
                    name = item.get("provider_name")
                    if name and name not in seen_names:
                        seen_names.add(name)
                        logo = item.get("logo_path")
                        structured_providers.append({
                            "name": name,
                            "type": "Buy",
                            "logo_url": f"{TMDB_IMAGE_BASE}{logo}" if logo else None,
                            "watch_url": watch_link
                        })

                for item in country_data.get("rent", []):
                    name = item.get("provider_name")
                    if name and name not in seen_names:
                        seen_names.add(name)
                        logo = item.get("logo_path")
                        structured_providers.append({
                            "name": name,
                            "type": "Rent",
                            "logo_url": f"{TMDB_IMAGE_BASE}{logo}" if logo else None,
                            "watch_url": watch_link
                        })

                primary_platform = structured_providers[0]["name"] if structured_providers else None
                return watch_link, primary_platform, structured_providers
    except Exception as e:
        logger.warning(f"Error fetching watch providers for {endpoint} {tmdb_id}: {e}")

    return None, None, []


def backfill_ott():
    db: Session = SessionLocal()
    try:
        movies = db.query(Movie).all()
        series = db.query(WebSeries).all()
        logger.info(f"Backfilling OTT availability for {len(movies)} movies and {len(series)} series...")

        updated_movies = 0
        updated_series = 0

        with httpx.Client(timeout=12.0) as client:
            for m in movies:
                if m.tmdb_id:
                    link, platform, providers = fetch_providers(client, m.tmdb_id, is_tv=False)
                    if providers or link or platform:
                        m.ott_url = link or m.ott_url
                        m.ott_platform = platform or m.ott_platform
                        m.ott_providers = providers
                        updated_movies += 1
                elif not m.ott_providers and m.title:
                    # Generic legal stream fallback
                    m.ott_url = f"https://www.justwatch.com/us/search?q={m.title.replace(' ', '%20')}"
                    m.ott_platform = "Prime Video / Digital VOD"
                    m.ott_providers = [{
                        "name": "Prime Video",
                        "type": "Stream / VOD",
                        "logo_url": None,
                        "watch_url": m.ott_url
                    }]
                    updated_movies += 1

            for s in series:
                if s.tmdb_id:
                    link, platform, providers = fetch_providers(client, s.tmdb_id, is_tv=True)
                    if providers or link or platform:
                        s.ott_url = link or s.ott_url
                        s.ott_platform = platform or s.ott_platform
                        s.ott_providers = providers
                        updated_series += 1
                elif not s.ott_providers and s.title:
                    s.ott_url = f"https://www.justwatch.com/us/search?q={s.title.replace(' ', '%20')}"
                    s.ott_platform = "Netflix / Prime Video"
                    s.ott_providers = [{
                        "name": "Netflix",
                        "type": "Stream",
                        "logo_url": None,
                        "watch_url": s.ott_url
                    }]
                    updated_series += 1

            db.commit()
            logger.info(f"Successfully backfilled OTT availability: {updated_movies} movies and {updated_series} series updated!")

    except Exception as e:
        logger.error(f"Error backfilling OTT: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    backfill_ott()
