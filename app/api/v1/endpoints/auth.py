from datetime import datetime, timedelta, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.session import get_db
from app.db.models import User
from app.schemas.auth import (
    UserRegisterRequest,
    LoginRequest,
    RefreshTokenRequest,
    CheckPasswordStrengthRequest,
    TokenResponse,
    MessageResponse,
    UserOut,
    PasswordStrengthResponse,
    UpdateUserRoleRequest,
)
from app.core.security import (
    hash_password,
    verify_password,
    validate_password_policy,
    create_access_token,
    create_refresh_token,
    decode_token,
)
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


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED, summary="User Registration")
async def register(req: UserRegisterRequest, db: Session = Depends(get_db)):
    """
    Registers a new user account and returns JWT credentials immediately.
    """
    is_valid, score, feedback = validate_password_policy(req.password)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Password does not meet policy requirements.", "issues": feedback}
        )

    stmt = select(User).where(User.email == req.email.lower())
    existing_user = db.execute(stmt).scalars().first()

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is already registered. Please login instead."
        )

    user = User(
        full_name=req.full_name,
        email=req.email.lower(),
        hashed_password=hash_password(req.password),
        is_active=True,
        role="user"
    )
    db.add(user)
    db.commit()
    db.refresh(user)

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

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account is deactivated."
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


@router.get("/users", response_model=List[UserOut], summary="List All System Users (Admin / Owner required)")
async def get_all_users(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    actor_role = current_user.role or "user"
    if actor_role not in ["admin", "owner"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Admin or Owner role required to list system users."
        )

    stmt = select(User).order_by(User.id.asc())
    users = db.execute(stmt).scalars().all()
    return [UserOut.model_validate(u) for u in users]


@router.patch("/users/role", response_model=UserOut, summary="Update User Role (Admin / Owner permissions)")
async def update_user_role(
    req: UpdateUserRoleRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    actor_role = current_user.role or "user"

    # 1. Simple users cannot modify any role
    if actor_role == "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Simple users cannot change user roles."
        )

    stmt = select(User).where(User.id == req.target_user_id)
    target_user = db.execute(stmt).scalars().first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target user not found."
        )

    target_current_role = target_user.role or "user"
    new_role = req.new_role

    # 2. Logic for Admin Actor:
    if actor_role == "admin":
        # Cannot modify an Owner
        if target_current_role == "owner":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Admin cannot modify an Owner user."
            )
        # Cannot assign 'owner' role
        if new_role == "owner":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Admin cannot grant the Owner role."
            )
        # Cannot demote an admin to user (only promotion from 'user' -> 'admin' is allowed)
        if target_current_role == "admin" and new_role == "user":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Predefined admins can change simple users to admin, but NOT the reverse."
            )
        # Only allowed: target_current_role == 'user' and new_role == 'admin'
        if target_current_role == "user" and new_role == "admin":
            target_user.role = "admin"
        elif target_current_role == new_role:
            pass  # no-op
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Admin can only promote simple users to admin role."
            )

    # 3. Logic for Owner Actor:
    elif actor_role == "owner":
        # Owner can change the role of any user to anything ('user', 'admin', 'owner')
        target_user.role = new_role

    db.commit()
    db.refresh(target_user)
    return UserOut.model_validate(target_user)
