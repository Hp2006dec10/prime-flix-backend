import logging
import time
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.db.models import Movie, WebSeries
from app.services.genre_service import (
    batch_analyze_stories_with_gemini,
    analyze_story_lexical
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("genre_backfill")


def run_backfill(batch_size: int = 15):
    db: Session = SessionLocal()
    try:
        movies = db.query(Movie).all()
        series = db.query(WebSeries).all()

        total_items = len(movies) + len(series)
        logger.info(f"Starting genre percentage calculation for {total_items} titles ({len(movies)} movies, {len(series)} series)...")

        # Prepare unified item list
        items_to_process = []
        for m in movies:
            items_to_process.append({
                "id": m.id,
                "title": m.title,
                "description": m.description or "",
                "genres": m.genre or "",
                "db_obj": m,
                "type": "movie"
            })
        for s in series:
            items_to_process.append({
                "id": s.series_id,
                "title": s.title,
                "description": s.description or "",
                "genres": s.genre or "",
                "db_obj": s,
                "type": "series"
            })

        # Process in batches
        processed_count = 0
        ai_success_count = 0
        lexical_count = 0

        for i in range(0, len(items_to_process), batch_size):
            batch = items_to_process[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (len(items_to_process) + batch_size - 1) // batch_size
            logger.info(f"Processing Batch {batch_num}/{total_batches} ({len(batch)} titles)...")

            # Try Gemini AI batch first
            ai_results = batch_analyze_stories_with_gemini(batch)

            for item in batch:
                obj = item["db_obj"]
                res_percentages = ai_results.get(item["id"])

                if res_percentages and isinstance(res_percentages, dict) and sum(res_percentages.values()) == 100:
                    obj.genre_percentages = res_percentages
                    ai_success_count += 1
                else:
                    # Fallback to story lexical analysis
                    fallback_percentages = analyze_story_lexical(
                        title=item["title"],
                        description=item["description"],
                        base_genre_str=item["genres"]
                    )
                    obj.genre_percentages = fallback_percentages
                    lexical_count += 1

                processed_count += 1
                logger.debug(f"Title '{item['title']}': {obj.genre_percentages}")

            # Commit batch to DB
            db.commit()
            logger.info(f"Saved Batch {batch_num}/{total_batches}. Progress: {processed_count}/{total_items}")

            # Brief pause between Gemini batches to respect rate limits if using AI
            if ai_results:
                time.sleep(1.0)

        logger.info("==================================================")
        logger.info(f"Successfully backfilled genre percentages for ALL {processed_count} titles!")
        logger.info(f"AI-Analyzed with Gemini: {ai_success_count} titles")
        logger.info(f"Story NLP Analyzed: {lexical_count} titles")
        logger.info("==================================================")

        # Verification check
        missing_movies = db.query(Movie).filter(Movie.genre_percentages == None).count()
        missing_series = db.query(WebSeries).filter(WebSeries.genre_percentages == None).count()
        logger.info(f"Verification - Missing movies: {missing_movies}, Missing series: {missing_series}")

    except Exception as e:
        logger.error(f"Error during genre percentage backfill: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    run_backfill()
