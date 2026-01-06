"""OIDC authentication helpers for API requests."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Optional
from uuid import UUID, uuid4

import jwt
from jwt import InvalidTokenError, PyJWKClient
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db.models import DEFAULT_TEST_USER_ID, User
from .db.session import get_session


bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class AuthUser:
    id: UUID
    subject: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    claims: Optional[dict[str, Any]] = None


class OIDCVerifier:
    def __init__(
        self,
        jwks_url: str,
        issuer: Optional[str],
        audience: Optional[str],
        algorithms: list[str],
        leeway_seconds: int,
    ) -> None:
        self._jwk_client = PyJWKClient(jwks_url)
        self._issuer = issuer
        self._audience = audience
        self._algorithms = algorithms
        self._leeway_seconds = leeway_seconds

    def decode(self, token: str) -> dict[str, Any]:
        signing_key = self._jwk_client.get_signing_key_from_jwt(token)
        options = {
            "verify_aud": bool(self._audience),
            "verify_iss": bool(self._issuer),
        }
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=self._algorithms,
            audience=self._audience,
            issuer=self._issuer,
            options=options,
            leeway=self._leeway_seconds,
        )


@lru_cache(maxsize=1)
def get_oidc_verifier() -> OIDCVerifier:
    settings = get_settings()
    jwks_url = settings.oidc_jwks_url
    if not jwks_url:
        raise RuntimeError("OIDC_JWKS_URL must be configured when auth is enabled.")
    issuer = str(settings.oidc_issuer_url) if settings.oidc_issuer_url else None
    return OIDCVerifier(
        jwks_url=str(jwks_url),
        issuer=issuer,
        audience=settings.oidc_audience,
        algorithms=settings.oidc_algorithms,
        leeway_seconds=settings.oidc_leeway_seconds,
    )


def _parse_uuid(value: Optional[str]) -> Optional[UUID]:
    if not value:
        return None
    try:
        return UUID(value)
    except (TypeError, ValueError):
        return None


async def _ensure_user_by_id(
    session: AsyncSession,
    user_id: UUID,
    email: Optional[str],
    display_name: Optional[str],
) -> User:
    user = await session.get(User, user_id)
    created = False
    if user is None:
        user = User(id=user_id, email=email, display_name=display_name)
        session.add(user)
        created = True
    else:
        if email and user.email != email:
            user.email = email
        if display_name and user.display_name != display_name:
            user.display_name = display_name
    if created or session.is_modified(user):
        await session.commit()
    return user


async def _ensure_user_by_email(
    session: AsyncSession,
    email: str,
    display_name: Optional[str],
) -> User:
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(id=uuid4(), email=email, display_name=display_name)
        session.add(user)
        await session.commit()
        return user
    if display_name and user.display_name != display_name:
        user.display_name = display_name
        await session.commit()
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> AuthUser:
    settings = get_settings()
    if not settings.auth_enabled:
        return AuthUser(id=DEFAULT_TEST_USER_ID, subject="local")

    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail="Missing authentication credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    try:
        payload = get_oidc_verifier().decode(token)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    subject_claim = settings.oidc_user_id_claim or "sub"
    subject_value = payload.get(subject_claim) or payload.get("sub")
    subject = str(subject_value) if subject_value is not None else ""

    email = payload.get(settings.oidc_email_claim) if settings.oidc_email_claim else None
    display_name = payload.get(settings.oidc_name_claim) if settings.oidc_name_claim else None
    if not display_name:
        display_name = payload.get("preferred_username") or email

    user_id = _parse_uuid(subject)
    if user_id:
        user = await _ensure_user_by_id(session, user_id, email, display_name)
        return AuthUser(
            id=user.id,
            subject=subject,
            email=user.email,
            display_name=user.display_name,
            claims=payload,
        )

    if email:
        user = await _ensure_user_by_email(session, email, display_name)
        return AuthUser(
            id=user.id,
            subject=subject or email,
            email=user.email,
            display_name=user.display_name,
            claims=payload,
        )

    raise HTTPException(
        status_code=401,
        detail="Token is missing subject or email claim.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user_id(user: AuthUser = Depends(get_current_user)) -> UUID:
    return user.id

