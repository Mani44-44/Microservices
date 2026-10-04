import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, ConfigDict


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class UserRegister(BaseModel):
    """Payload for new user registration."""

    email: EmailStr
    password: str = Field(..., min_length=8, description="Minimum 8 characters")
    full_name: str = Field(..., min_length=1, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)


class UserLogin(BaseModel):
    """Payload for user login."""

    email: EmailStr
    password: str


class UserUpdate(BaseModel):
    """Payload for partial profile updates."""

    full_name: Optional[str] = Field(None, min_length=1, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)


class RefreshRequest(BaseModel):
    """Payload carrying a refresh token."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """Payload for logout — revokes the supplied refresh token."""

    refresh_token: str


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class UserProfile(BaseModel):
    """Public user profile returned to the client."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    phone: Optional[str]
    created_at: datetime
    is_active: bool


class TokenPair(BaseModel):
    """JWT access token + opaque refresh token pair."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until access token expiry


# ---------------------------------------------------------------------------
# Error schemas
# ---------------------------------------------------------------------------


class ErrorDetail(BaseModel):
    """Structured error payload."""

    code: str
    message: str
    details: dict = Field(default_factory=dict)
    correlation_id: str


class ErrorResponse(BaseModel):
    """Top-level error envelope returned by all error responses."""

    error: ErrorDetail
