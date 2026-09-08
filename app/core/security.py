import secrets
import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple, List
import bcrypt
import jwt
from app.core.config import settings


def hash_password(password: str) -> str:
    """Hash password using bcrypt directly (safe against passlib 72-byte issue)."""
    pwd_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against hashed password using bcrypt directly."""
    try:
        pwd_bytes = plain_password.encode("utf-8")[:72]
        hash_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(pwd_bytes, hash_bytes)
    except Exception:
        return False


def validate_password_policy(password: str) -> Tuple[bool, int, List[str]]:
    """
    Validates password against policy rules:
    - Minimum 8 characters
    - At least 1 uppercase letter
    - At least 1 lowercase letter
    - At least 1 number
    - At least 1 special character

    Returns:
        (is_valid, score, feedback_list)
        score range: 0 (Very Weak) to 4 (Very Strong)
    """
    feedback = []
    
    if len(password) < 8:
        feedback.append("Password must be at least 8 characters long.")
    if not re.search(r"[A-Z]", password):
        feedback.append("Password must contain at least one uppercase letter (A-Z).")
    if not re.search(r"[a-z]", password):
        feedback.append("Password must contain at least one lowercase letter (a-z).")
    if not re.search(r"[0-9]", password):
        feedback.append("Password must contain at least one numeric digit (0-9).")
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?]", password):
        feedback.append("Password must contain at least one special character (e.g. !@#$%^&*).")

    # Score calculation
    score = 0
    if len(password) >= 8:
        score += 1
    if len(password) >= 12:
        score += 1
    if re.search(r"[A-Z]", password) and re.search(r"[a-z]", password):
        score += 1
    if re.search(r"[0-9]", password) and re.search(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?]", password):
        score += 1

    is_valid = len(feedback) == 0
    return is_valid, score, feedback


def generate_otp() -> str:
    """
    Generates a cryptographically secure 6-digit numeric OTP.
    """
    number = secrets.randbelow(1000000)
    return f"{number:06d}"


def hash_otp(otp: str) -> str:
    """
    Hashes OTP string using SHA-256 with JWT_SECRET as key to ensure
    only hashed OTP values are stored in DB.
    """
    return hashlib.sha256(f"{otp}:{settings.JWT_SECRET}".encode("utf-8")).hexdigest()


def verify_otp(otp: str, hashed_otp: str) -> bool:
    """
    Verifies input OTP string against stored hashed OTP.
    """
    return secrets.compare_digest(hash_otp(otp), hashed_otp)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Generates JWT Access Token.
    """
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({
        "exp": expire,
        "iat": now,
        "type": "access"
    })
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Generates JWT Refresh Token.
    """
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    to_encode.update({
        "exp": expire,
        "iat": now,
        "type": "refresh"
    })
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Decodes and verifies a JWT token.
    Returns payload dictionary or None if invalid/expired.
    """
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None
