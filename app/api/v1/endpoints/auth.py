from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select, and_

from app.db.session import get_db
from app.db.models import User, OTP
from app.schemas.auth import (
    UserRegisterRequest,
    VerifyOTPRequest,
    LoginRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    ResendOTPRequest,
    RefreshTokenRequest,
    CheckPasswordStrengthRequest,
    TokenResponse,
    MessageResponse,
    UserOut,
    PasswordStrengthResponse,
)
from app.core.security import (
    hash_password,
    verify_password,
    validate_password_policy,
    generate_otp,
    hash_otp,
    verify_otp,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.services.email import send_otp_email
from app.api.deps import get_current_user

router = APIRouter()


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.post("/check-password-strength", response_model=PasswordStrengthResponse, summary="Check Password Policy & Strength")
async def check_password_strength(req: CheckPasswordStrengthRequest):
    """
    Evaluates a password against system policy and returns strength score and feedback.
    """
    is_valid, score, feedback = validate_password_policy(req.password)
    return PasswordStrengthResponse(
        is_valid=is_valid,
        score=score,
        feedback=feedback
    )


@router.post("/register", response_model=MessageResponse, status_code=status.HTTP_201_CREATED, summary="User Registration")
async def register(req: UserRegisterRequest, db: Session = Depends(get_db)):
    """
    Registers a new user account and sends an email verification OTP.
    """
    is_valid, score, feedback = validate_password_policy(req.password)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Password does not meet policy requirements.", "issues": feedback}
        )

    stmt = select(User).where(User.email == req.email.lower())
    existing_user = db.execute(stmt).scalars().first()

    now = now_utc()

    if existing_user:
        if existing_user.is_verified:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email is already registered. Please login instead."
            )
        
        otp_stmt = select(OTP).where(
            and_(
                OTP.user_id == existing_user.id,
                OTP.otp_type == "registration",
                OTP.is_used == False,
                OTP.expires_at > now
            )
        ).order_by(OTP.created_at.desc())
        active_otp = db.execute(otp_stmt).scalars().first()

        if active_otp and (active_otp.locked_until is None or active_otp.locked_until <= now):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A valid OTP already sent. Please use that OTP instead."
            )

        existing_user.full_name = req.full_name
        existing_user.hashed_password = hash_password(req.password)
        user = existing_user
    else:
        user = User(
            full_name=req.full_name,
            email=req.email.lower(),
            hashed_password=hash_password(req.password),
            is_active=False,
            is_verified=False
        )
        db.add(user)
        db.flush()

    otp_code = generate_otp()
    hashed = hash_otp(otp_code)
    expires_at = now + timedelta(minutes=10)

    deactivate_stmt = select(OTP).where(
        and_(OTP.user_id == user.id, OTP.otp_type == "registration", OTP.is_used == False)
    )
    for old_otp in db.execute(deactivate_stmt).scalars().all():
        old_otp.is_used = True

    new_otp = OTP(
        user_id=user.id,
        hashed_otp=hashed,
        otp_type="registration",
        expires_at=expires_at,
        is_used=False
    )
    db.add(new_otp)
    db.commit()

    await send_otp_email(user.email, otp_code, purpose="registration")

    return MessageResponse(
        message="Registration successful. A verification OTP has been sent to your email.",
        otp_active=False
    )


@router.post("/verify-registration-otp", response_model=TokenResponse, summary="Verify Registration OTP")
async def verify_registration_otp(req: VerifyOTPRequest, db: Session = Depends(get_db)):
    """
    Verifies 6-digit OTP for user registration.
    """
    stmt = select(User).where(User.email == req.email.lower())
    user = db.execute(stmt).scalars().first()

    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User account not found.")

    if user.is_verified:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account is already verified. Please login.")

    now = now_utc()

    otp_stmt = select(OTP).where(
        and_(OTP.user_id == user.id, OTP.otp_type == "registration")
    ).order_by(OTP.created_at.desc())
    otp_record = db.execute(otp_stmt).scalars().first()

    if not otp_record or otp_record.is_used:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active OTP found. Please request a new OTP.")

    if otp_record.locked_until and otp_record.locked_until > now:
        remaining_lock = int((otp_record.locked_until - now).total_seconds() // 60)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"OTP verification locked due to 5 failed attempts. Please try again in {remaining_lock} minutes."
        )

    if otp_record.expires_at < now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OTP has expired. Please request a new OTP.")

    if not verify_otp(req.otp, otp_record.hashed_otp):
        otp_record.failed_attempts += 1
        if otp_record.failed_attempts >= 5:
            otp_record.locked_until = now + timedelta(minutes=30)
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Maximum verification attempts (5) exceeded. OTP verification locked for 30 minutes."
            )
        remaining = 5 - otp_record.failed_attempts
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid OTP code. {remaining} attempt(s) remaining."
        )

    otp_record.is_used = True
    user.is_verified = True
    user.is_active = True
    user.failed_login_attempts = 0
    user.login_locked_until = None
    db.commit()

    access_token = create_access_token({"sub": str(user.id), "email": user.email})
    refresh_token = create_refresh_token({"sub": str(user.id), "email": user.email})

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user=UserOut.model_validate(user)
    )


@router.post("/login", response_model=TokenResponse, summary="User Login")
async def login(req: LoginRequest, db: Session = Depends(get_db)):
    """
    Authenticates user with email & password.
    """
    stmt = select(User).where(User.email == req.email.lower())
    user = db.execute(stmt).scalars().first()

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

    now = now_utc()

    if user.login_locked_until and user.login_locked_until > now:
        remaining_lock = int((user.login_locked_until - now).total_seconds() // 60)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Account is locked due to repeated failed login attempts. Please try again after {remaining_lock} minutes."
        )

    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account email is not verified. Please verify your email first."
        )

    if not verify_password(req.password, user.hashed_password):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= 3:
            user.login_locked_until = now + timedelta(minutes=30)
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Account locked for 30 minutes due to 3 failed login attempts."
            )
        remaining = 3 - user.failed_login_attempts
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid email or password. {remaining} attempt(s) remaining before lockout."
        )

    user.failed_login_attempts = 0
    user.login_locked_until = None
    db.commit()

    access_token = create_access_token({"sub": str(user.id), "email": user.email})
    refresh_token = create_refresh_token({"sub": str(user.id), "email": user.email})

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user=UserOut.model_validate(user)
    )


@router.post("/forgot-password", response_model=MessageResponse, summary="Request Password Reset OTP")
async def forgot_password(req: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """
    Sends a password reset OTP.
    """
    stmt = select(User).where(User.email == req.email.lower())
    user = db.execute(stmt).scalars().first()

    if not user or not user.is_verified:
        return MessageResponse(message="If an account with that email exists, a password reset code has been sent.")

    now = now_utc()

    otp_stmt = select(OTP).where(
        and_(
            OTP.user_id == user.id,
            OTP.otp_type == "forgot_password",
            OTP.is_used == False,
            OTP.expires_at > now
        )
    ).order_by(OTP.created_at.desc())
    active_otp = db.execute(otp_stmt).scalars().first()

    if active_otp and (active_otp.locked_until is None or active_otp.locked_until <= now):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid OTP already sent. Please use that OTP instead."
        )

    otp_code = generate_otp()
    hashed = hash_otp(otp_code)
    expires_at = now + timedelta(minutes=10)

    deactivate_stmt = select(OTP).where(
        and_(OTP.user_id == user.id, OTP.otp_type == "forgot_password", OTP.is_used == False)
    )
    for old_otp in db.execute(deactivate_stmt).scalars().all():
        old_otp.is_used = True

    new_otp = OTP(
        user_id=user.id,
        hashed_otp=hashed,
        otp_type="forgot_password",
        expires_at=expires_at,
        is_used=False
    )
    db.add(new_otp)
    db.commit()

    await send_otp_email(user.email, otp_code, purpose="forgot_password")

    return MessageResponse(
        message="A password reset OTP has been sent to your email.",
        otp_active=False
    )


@router.post("/reset-password", response_model=MessageResponse, summary="Reset Password with OTP")
async def reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)):
    """
    Verifies reset OTP and updates user password.
    """
    is_valid, score, feedback = validate_password_policy(req.new_password)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "New password does not meet policy requirements.", "issues": feedback}
        )

    stmt = select(User).where(User.email == req.email.lower())
    user = db.execute(stmt).scalars().first()

    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User account not found.")

    now = now_utc()

    otp_stmt = select(OTP).where(
        and_(OTP.user_id == user.id, OTP.otp_type == "forgot_password")
    ).order_by(OTP.created_at.desc())
    otp_record = db.execute(otp_stmt).scalars().first()

    if not otp_record or otp_record.is_used:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active password reset request found.")

    if otp_record.locked_until and otp_record.locked_until > now:
        remaining_lock = int((otp_record.locked_until - now).total_seconds() // 60)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Reset OTP verification locked due to failed attempts. Try again in {remaining_lock} minutes."
        )

    if otp_record.expires_at < now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset OTP has expired. Please request a new code.")

    if not verify_otp(req.otp, otp_record.hashed_otp):
        otp_record.failed_attempts += 1
        if otp_record.failed_attempts >= 5:
            otp_record.locked_until = now + timedelta(minutes=30)
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Maximum reset verification attempts (5) exceeded. Locked for 30 minutes."
            )
        remaining = 5 - otp_record.failed_attempts
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid OTP code. {remaining} attempt(s) remaining."
        )

    otp_record.is_used = True
    user.hashed_password = hash_password(req.new_password)
    user.failed_login_attempts = 0
    user.login_locked_until = None
    db.commit()

    return MessageResponse(message="Password has been reset successfully. You can now login with your new password.")


@router.post("/resend-otp", response_model=MessageResponse, summary="Resend OTP")
async def resend_otp(req: ResendOTPRequest, db: Session = Depends(get_db)):
    """
    Resends OTP if current OTP has expired or been used.
    """
    stmt = select(User).where(User.email == req.email.lower())
    user = db.execute(stmt).scalars().first()

    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User account not found.")

    now = now_utc()

    otp_stmt = select(OTP).where(
        and_(
            OTP.user_id == user.id,
            OTP.otp_type == req.otp_type,
            OTP.is_used == False,
            OTP.expires_at > now
        )
    ).order_by(OTP.created_at.desc())
    active_otp = db.execute(otp_stmt).scalars().first()

    if active_otp and (active_otp.locked_until is None or active_otp.locked_until <= now):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid OTP already sent. Please use that OTP instead."
        )

    otp_code = generate_otp()
    hashed = hash_otp(otp_code)
    expires_at = now + timedelta(minutes=10)

    deactivate_stmt = select(OTP).where(
        and_(OTP.user_id == user.id, OTP.otp_type == req.otp_type, OTP.is_used == False)
    )
    for old_otp in db.execute(deactivate_stmt).scalars().all():
        old_otp.is_used = True

    new_otp = OTP(
        user_id=user.id,
        hashed_otp=hashed,
        otp_type=req.otp_type,
        expires_at=expires_at,
        is_used=False
    )
    db.add(new_otp)
    db.commit()

    await send_otp_email(user.email, otp_code, purpose=req.otp_type)

    return MessageResponse(message="A new OTP has been sent to your email.", otp_active=False)


@router.post("/refresh", response_model=TokenResponse, summary="Refresh Access Token")
async def refresh_token(req: RefreshTokenRequest, db: Session = Depends(get_db)):
    """
    Issues a new access token using a valid refresh token.
    """
    payload = decode_token(req.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token."
        )

    user_id = payload.get("sub")
    stmt = select(User).where(User.id == int(user_id))
    user = db.execute(stmt).scalars().first()

    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user account.")

    new_access_token = create_access_token({"sub": str(user.id), "email": user.email})
    new_refresh_token = create_refresh_token({"sub": str(user.id), "email": user.email})

    return TokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        token_type="bearer",
        user=UserOut.model_validate(user)
    )


@router.get("/me", response_model=UserOut, summary="Get Current Authenticated User Profile")
async def get_me(current_user: User = Depends(get_current_user)):
    """
    Protected endpoint that returns current authenticated user details.
    """
    return UserOut.model_validate(current_user)


@router.post("/logout", response_model=MessageResponse, summary="Logout User Session")
async def logout(current_user: User = Depends(get_current_user)):
    """
    Protected endpoint to handle session logout.
    """
    return MessageResponse(message="Successfully logged out.")
