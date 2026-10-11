from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Float, Text
from sqlalchemy.orm import relationship
from app.db.base import Base


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    full_name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=False, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    role = Column(String(50), default="user", nullable=False)  # 'user', 'admin', 'owner'
    
    # Brute force login lockout tracking
    failed_login_attempts = Column(Integer, default=0, nullable=False)
    login_locked_until = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    otps = relationship("OTP", back_populates="user", cascade="all, delete-orphan")


class OTP(Base):
    __tablename__ = "otps"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    hashed_otp = Column(String(255), nullable=False)
    otp_type = Column(String(50), nullable=False)  # 'registration' or 'forgot_password'
    expires_at = Column(DateTime, nullable=False)
    
    # Brute force OTP verification lockout tracking
    failed_attempts = Column(Integer, default=0, nullable=False)
    locked_until = Column(DateTime, nullable=True)
    
    is_used = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    user = relationship("User", back_populates="otps")


class Director(Base):
    __tablename__ = "directors"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    nationality = Column(String(255), nullable=True)

    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    movies = relationship("Movie", back_populates="director", cascade="all, delete-orphan")
    webseries = relationship("WebSeries", back_populates="director")


class Movie(Base):
    __tablename__ = "movies"

    id = Column(String(50), primary_key=True, index=True)  # Format e.g. 'MOV-1', 'MOV-100'
    tmdb_id = Column(Integer, unique=True, index=True, nullable=True)
    imdb_id = Column(String(50), unique=True, index=True, nullable=True)
    title = Column(String(255), nullable=False, index=True)
    average_rating = Column(Float, nullable=True)
    description = Column(Text, nullable=True)
    duration = Column(Integer, nullable=True)  # in minutes
    release_year = Column(Integer, nullable=True)
    poster_image_url = Column(Text, nullable=True)
    genre = Column(String(255), nullable=True)
    language = Column(String(100), nullable=True)
    director_id = Column(Integer, ForeignKey("directors.id", ondelete="SET NULL"), nullable=True)
    ott_url = Column(Text, nullable=True)
    ott_platform = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    director = relationship("Director", back_populates="movies")


class CinematicUniverse(Base):
    __tablename__ = "cinematic_universes"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)

    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    contents = relationship("UniverseContent", back_populates="universe", cascade="all, delete-orphan")


class UniverseContent(Base):
    __tablename__ = "universe_contents"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    universe_id = Column(Integer, ForeignKey("cinematic_universes.id", ondelete="CASCADE"), nullable=False, index=True)
    content_id = Column(String(50), nullable=False, index=True)  # Identifier like 'MOV-1' or 'SER-1'
    content_type = Column(String(50), default="movie", nullable=False)  # 'movie' or 'web_series'
    order_in_universe = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=utcnow, nullable=False)

    universe = relationship("CinematicUniverse", back_populates="contents")


class WebSeries(Base):
    __tablename__ = "webseries"

    series_id = Column(String(50), primary_key=True, index=True)  # Format e.g. 'SER-1', 'SER-1396'
    tmdb_id = Column(Integer, unique=True, index=True, nullable=True)
    imdb_id = Column(String(50), unique=True, index=True, nullable=True)
    title = Column(String(255), nullable=False, index=True)
    average_rating = Column(Float, nullable=True)
    description = Column(Text, nullable=True)
    release_year = Column(Integer, nullable=True)
    total_seasons = Column(Integer, default=1, nullable=False)
    poster_image_url = Column(Text, nullable=True)
    genre = Column(String(255), nullable=True)
    language = Column(String(100), nullable=True)
    director_id = Column(Integer, ForeignKey("directors.id", ondelete="SET NULL"), nullable=True)
    ott_url = Column(Text, nullable=True)
    ott_platform = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    director = relationship("Director", back_populates="webseries")
    seasons = relationship("Season", back_populates="webseries", cascade="all, delete-orphan", order_by="Season.season_number")


class Season(Base):
    __tablename__ = "seasons"

    season_id = Column(String(50), primary_key=True, index=True)  # Format e.g. 'SEAS-1396-1'
    series_id = Column(String(50), ForeignKey("webseries.series_id", ondelete="CASCADE"), nullable=False, index=True)
    season_number = Column(Integer, nullable=False)
    release_year = Column(Integer, nullable=True)
    title = Column(String(255), nullable=True)  # e.g. 'Season 1'
    description = Column(Text, nullable=True)
    poster_image_url = Column(Text, nullable=True)
    episode_count = Column(Integer, nullable=True)
    tmdb_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    webseries = relationship("WebSeries", back_populates="seasons")
    episodes = relationship("Episode", back_populates="season", cascade="all, delete-orphan", order_by="Episode.episode_number")


class Episode(Base):
    __tablename__ = "episodes"

    episode_id = Column(String(50), primary_key=True, index=True)  # Format e.g. 'EP-1396-1-1'
    season_id = Column(String(50), ForeignKey("seasons.season_id", ondelete="CASCADE"), nullable=False, index=True)
    episode_number = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False)
    duration = Column(Integer, nullable=True)  # in minutes
    description = Column(Text, nullable=True)
    still_image_url = Column(Text, nullable=True)
    average_rating = Column(Float, nullable=True)
    release_date = Column(String(50), nullable=True)
    tmdb_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    season = relationship("Season", back_populates="episodes")


