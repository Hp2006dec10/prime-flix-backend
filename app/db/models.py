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


