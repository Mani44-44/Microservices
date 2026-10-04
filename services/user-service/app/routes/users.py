"""User-facing routes for the User Service.

Endpoints
---------
POST   /register   – create a new account, return token pair (201)
POST   /login      – authenticate, return token pair (200)
POST   /refresh    – rotate refresh token, return new token pair (200)
POST   /logout     – revoke a refresh token (200)
GET    /me         – return the authenticated user's profile (200)
PATCH  /me         – update full_name / phone (200)
GET    /health     – liveness probe (200)
"""

import uuid
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.encryption import decrypt_field, encrypt_field
from app.config import get_settings
from app.database import get_db
from app.models import RefreshToken, User
from app.schemas import (
    ErrorDetail,
    ErrorResponse,
    LogoutRequest,
    RefreshRequest,
    TokenPair,
    UserLogin,
    UserProfile,
    UserRegister,
    UserUpdate,
)

log = structlog.get_logger()
settings = get_settings()
bearer_scheme = HTTPBearer(auto_error=False)

router = APIRouter()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _correlation_id(request: Request) -> str:
    """Extract the correlation ID injected by the middleware."""
    return getattr(request.state, "correlation_id", str(uuid.uuid4()))


def _error(
    status_code: int,
    code: str,
    message: str,
    correlation_id: str,
    details: dict | None = None,
) -> HTTPException:
    """Build a consistently formatted HTTPException."""
    return HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "correlation_id": correlation_id,
            }
        },
    )


def _decrypted_profile(user: User) -> UserProfile:
    """Build a UserProfile with full_name/phone decrypted for API output."""
    return UserProfile(
        id=user.id,
        email=user.email,
        full_name=decrypt_field(user.full_name),
        phone=decrypt_field(user.phone) if user.phone is not None else None,
        is_active=user.is_active,
        created_at=user.created_at,
    )


async def _create_and_persist_refresh_token(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> str:
    """Generate a new refresh token, persist its hash, and return the raw token."""
    raw_token = generate_refresh_token()
    token_hash = hash_token(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.jwt_refresh_token_expire_days
    )
    db_token = RefreshToken(
        id=uuid.uuid4(),
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    db.add(db_token)
    await db.flush()  # persist so we can get the id before commit
    return raw_token, db_token


async def _get_current_user_from_bearer(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    db: AsyncSession,
) -> User:
    """Decode JWT bearer token and return the corresponding User row."""
    cid = _correlation_id(request)
    if credentials is None:
        raise _error(
            status.HTTP_401_UNAUTHORIZED,
            "MISSING_TOKEN",
            "Authorization header with Bearer token is required.",
            cid,
        )
    payload = decode_access_token(credentials.credentials)
    user_id_str = payload.get("sub")
    if not user_id_str:
        raise _error(
            status.HTTP_401_UNAUTHORIZED,
            "TOKEN_INVALID",
            "Token is missing the 'sub' claim.",
            cid,
        )
    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise _error(
            status.HTTP_401_UNAUTHORIZED,
            "TOKEN_INVALID",
            "Token 'sub' claim is not a valid UUID.",
            cid,
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise _error(
            status.HTTP_401_UNAUTHORIZED,
            "USER_NOT_FOUND",
            "User associated with this token no longer exists or is inactive.",
            cid,
        )
    return user


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/health", tags=["health"])
async def health_check():
    """Liveness probe."""
    return {"status": "healthy", "service": "user-service"}


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=TokenPair,
    responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    tags=["auth"],
)
async def register(
    payload: UserRegister,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a new user account and return a token pair."""
    cid = _correlation_id(request)

    # Check for duplicate email
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none() is not None:
        log.warning("register_duplicate_email", email=payload.email, correlation_id=cid)
        raise _error(
            status.HTTP_409_CONFLICT,
            "EMAIL_ALREADY_EXISTS",
            f"An account with email '{payload.email}' already exists.",
            cid,
        )

    # Create user
    user = User(
        id=uuid.uuid4(),
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=encrypt_field(payload.full_name),
        phone=encrypt_field(payload.phone) if payload.phone is not None else None,
    )
    db.add(user)
    await db.flush()  # assign PK before creating the token

    # Create refresh token
    raw_refresh, _ = await _create_and_persist_refresh_token(db, user.id)

    # Access token
    access_token = create_access_token(user.id, user.email, user.role)
    expires_in = settings.jwt_access_token_expire_minutes * 60

    log.info("user_registered", user_id=str(user.id), correlation_id=cid)
    return TokenPair(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=expires_in,
    )


@router.post(
    "/login",
    status_code=status.HTTP_200_OK,
    response_model=TokenPair,
    responses={401: {"model": ErrorResponse}},
    tags=["auth"],
)
async def login(
    payload: UserLogin,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate with email + password and return a token pair."""
    cid = _correlation_id(request)

    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    # Use consistent error to prevent user enumeration
    if user is None or not verify_password(payload.password, user.password_hash):
        log.warning("login_failed", email=payload.email, correlation_id=cid)
        raise _error(
            status.HTTP_401_UNAUTHORIZED,
            "INVALID_CREDENTIALS",
            "Email or password is incorrect.",
            cid,
        )

    if not user.is_active:
        raise _error(
            status.HTTP_401_UNAUTHORIZED,
            "ACCOUNT_INACTIVE",
            "This account has been deactivated.",
            cid,
        )

    raw_refresh, _ = await _create_and_persist_refresh_token(db, user.id)
    access_token = create_access_token(user.id, user.email, user.role)
    expires_in = settings.jwt_access_token_expire_minutes * 60

    log.info("user_logged_in", user_id=str(user.id), correlation_id=cid)
    return TokenPair(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=expires_in,
    )


@router.post(
    "/refresh",
    status_code=status.HTTP_200_OK,
    response_model=TokenPair,
    responses={401: {"model": ErrorResponse}},
    tags=["auth"],
)
async def refresh_token(
    payload: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Rotate a refresh token: revoke old one, issue a new token pair."""
    cid = _correlation_id(request)
    token_hash = hash_token(payload.refresh_token)

    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    db_token = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)

    if db_token is None:
        log.warning("refresh_token_not_found", correlation_id=cid)
        raise _error(
            status.HTTP_401_UNAUTHORIZED,
            "INVALID_REFRESH_TOKEN",
            "Refresh token is invalid or has already been used.",
            cid,
        )

    if db_token.revoked_at is not None:
        # Possible token reuse attack – revoke the entire family
        log.warning(
            "refresh_token_reuse_detected",
            token_id=str(db_token.id),
            user_id=str(db_token.user_id),
            correlation_id=cid,
        )
        await db.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == db_token.user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        # get_db only commits on the success path; this route raises next,
        # so commit explicitly or the revocation above is rolled back.
        await db.commit()
        raise _error(
            status.HTTP_401_UNAUTHORIZED,
            "REFRESH_TOKEN_REUSE",
            "Refresh token has already been used. Please log in again.",
            cid,
        )

    if db_token.expires_at.replace(tzinfo=timezone.utc) < now:
        raise _error(
            status.HTTP_401_UNAUTHORIZED,
            "REFRESH_TOKEN_EXPIRED",
            "Refresh token has expired. Please log in again.",
            cid,
        )

    # Fetch the associated user
    result = await db.execute(select(User).where(User.id == db_token.user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise _error(
            status.HTTP_401_UNAUTHORIZED,
            "USER_NOT_FOUND",
            "User no longer exists or is inactive.",
            cid,
        )

    # Issue new token pair
    raw_refresh, new_db_token = await _create_and_persist_refresh_token(db, user.id)

    # Mark old token as revoked and link to successor
    db_token.revoked_at = now
    db_token.replaced_by_token_id = new_db_token.id
    db.add(db_token)

    access_token = create_access_token(user.id, user.email, user.role)
    expires_in = settings.jwt_access_token_expire_minutes * 60

    log.info(
        "refresh_token_rotated",
        user_id=str(user.id),
        old_token_id=str(db_token.id),
        new_token_id=str(new_db_token.id),
        correlation_id=cid,
    )
    return TokenPair(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=expires_in,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
    responses={401: {"model": ErrorResponse}},
    tags=["auth"],
)
async def logout(
    payload: LogoutRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Revoke a refresh token, effectively logging the client out."""
    cid = _correlation_id(request)
    token_hash = hash_token(payload.refresh_token)

    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    db_token = result.scalar_one_or_none()

    if db_token is None or db_token.revoked_at is not None:
        # Idempotent – already revoked or never existed
        return {"message": "Logged out successfully."}

    db_token.revoked_at = datetime.now(timezone.utc)
    db.add(db_token)

    log.info(
        "user_logged_out",
        user_id=str(db_token.user_id),
        token_id=str(db_token.id),
        correlation_id=cid,
    )
    return {"message": "Logged out successfully."}


@router.get(
    "/me",
    status_code=status.HTTP_200_OK,
    response_model=UserProfile,
    responses={401: {"model": ErrorResponse}},
    tags=["users"],
)
async def get_me(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
):
    """Return the authenticated user's profile."""
    user = await _get_current_user_from_bearer(request, credentials, db)
    return _decrypted_profile(user)


@router.patch(
    "/me",
    status_code=status.HTTP_200_OK,
    response_model=UserProfile,
    responses={401: {"model": ErrorResponse}},
    tags=["users"],
)
async def update_me(
    payload: UserUpdate,
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
):
    """Update the authenticated user's full_name and/or phone."""
    cid = _correlation_id(request)
    user = await _get_current_user_from_bearer(request, credentials, db)

    updated = False
    if payload.full_name is not None:
        user.full_name = encrypt_field(payload.full_name)
        updated = True
    if payload.phone is not None:
        user.phone = encrypt_field(payload.phone)
        updated = True

    if updated:
        user.updated_at = datetime.now(timezone.utc)
        db.add(user)
        log.info("user_profile_updated", user_id=str(user.id), correlation_id=cid)

    return _decrypted_profile(user)
