import logging
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.core.config import settings
from app.db.base import Base
import app.db.models  # Ensure models are registered with Base

logger = logging.getLogger("uvicorn.error")

# Create engine directly from settings.DATABASE_URL
engine = create_engine(settings.DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """
    Initializes database tables using DATABASE_URL directly.
    """
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized successfully.")


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency yielding database session per request.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
