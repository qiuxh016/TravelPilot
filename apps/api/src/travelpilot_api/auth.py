import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.user import UserModel, UserSessionModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel
from travelpilot_infra.db.models.workflow import ContentSourceModel
from travelpilot_infra.db.session import get_session

bearer = HTTPBearer(auto_error=False)
SESSION_HOURS = 12
SESSION_COOKIE = "travelpilot_session"
CSRF_COOKIE = "travelpilot_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=64)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


DUMMY_PASSWORD_HASH = hash_password("travelpilot-unknown-account-dummy")


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(digest_hex)),
        )
        return hmac.compare_digest(actual, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> UserModel:
    if credentials is not None and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
        using_cookie = False
    else:
        token = request.cookies.get(SESSION_COOKIE, "")
        using_cookie = bool(token)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required",
                            headers={"WWW-Authenticate": "Bearer"})
    if using_cookie and request.method.upper() not in SAFE_METHODS:
        cookie_csrf = request.cookies.get(CSRF_COOKIE, "")
        header_csrf = request.headers.get("x-csrf-token", "")
        if not cookie_csrf or not header_csrf or not hmac.compare_digest(cookie_csrf, header_csrf):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed")
    now = datetime.now(UTC)
    result = await session.execute(
        select(UserModel).join(UserSessionModel, UserSessionModel.user_id == UserModel.id)
        .where(UserSessionModel.token_hash == token_digest(token),
               UserSessionModel.expires_at > now, UserModel.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session",
                            headers={"WWW-Authenticate": "Bearer"})
    return user


def set_session_cookies(response: Response, token: str) -> None:
    from travelpilot_api.settings import settings

    secure = settings.app_env.lower() == "production"
    domain = settings.session_cookie_domain or None
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_HOURS * 60 * 60,
                        httponly=True, secure=secure, samesite="lax", path="/api/v1", domain=domain)
    response.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(24), max_age=SESSION_HOURS * 60 * 60,
                        httponly=False, secure=secure, samesite="lax", path="/", domain=domain)


def clear_session_cookies(response: Response) -> None:
    from travelpilot_api.settings import settings

    secure = settings.app_env.lower() == "production"
    domain = settings.session_cookie_domain or None
    response.delete_cookie(SESSION_COOKIE, path="/api/v1", secure=secure, httponly=True, samesite="lax", domain=domain)
    response.delete_cookie(CSRF_COOKIE, path="/", secure=secure, httponly=False, samesite="lax", domain=domain)


async def require_request_scope(
    request: Request,
    user: UserModel = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Require ownership for any trip-scoped or direct child-resource route."""
    trip_id = request.path_params.get("trip_id")
    if trip_id is not None:
        try:
            trip_id = UUID(str(trip_id))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Resource not found") from exc
        owned = await session.scalar(select(TripModel.id).where(
            TripModel.id == trip_id, TripModel.owner_id == user.id
        ))
        if owned is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return

    item_id = request.path_params.get("item_id")
    if item_id is not None:
        try:
            item_id = UUID(str(item_id))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Resource not found") from exc
        owned = await session.scalar(
            select(WishlistItemModel.id).join(TripModel, TripModel.id == WishlistItemModel.trip_id)
            .where(WishlistItemModel.id == item_id, TripModel.owner_id == user.id)
        )
        if owned is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return

    content_id = request.path_params.get("content_id")
    if content_id is not None:
        try:
            content_id = UUID(str(content_id))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Resource not found") from exc
        owned = await session.scalar(
            select(ContentSourceModel.id).join(TripModel, TripModel.id == ContentSourceModel.trip_id)
            .where(ContentSourceModel.id == content_id, TripModel.owner_id == user.id)
        )
        if owned is None:
            raise HTTPException(status_code=404, detail="Resource not found")


def new_session_token() -> str:
    return secrets.token_urlsafe(32)
