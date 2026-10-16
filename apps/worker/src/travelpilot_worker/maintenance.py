"""Periodic cleanup for authentication records with explicit expiry rules."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from travelpilot_api.settings import settings
from travelpilot_infra.db.models.user import EmailTokenModel, LoginAttemptModel, UserSessionModel
from travelpilot_infra.db.session import AsyncSessionFactory


async def cleanup_expired_auth_data(
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionFactory,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Delete expired sessions/tokens and stale login throttling records."""
    current = now or datetime.now(UTC)
    consumed_token_cutoff = current - timedelta(hours=24)
    login_attempt_cutoff = current - timedelta(
        minutes=settings.login_window_minutes + 24 * 60
    )

    async with session_factory() as session:
        sessions = await session.execute(
            delete(UserSessionModel).where(UserSessionModel.expires_at <= current)
        )
        tokens = await session.execute(
            delete(EmailTokenModel).where(
                or_(
                    EmailTokenModel.expires_at <= current,
                    EmailTokenModel.consumed_at <= consumed_token_cutoff,
                )
            )
        )
        attempts = await session.execute(
            delete(LoginAttemptModel).where(
                LoginAttemptModel.window_started_at <= login_attempt_cutoff,
                or_(LoginAttemptModel.blocked_until.is_(None),
                    LoginAttemptModel.blocked_until <= current),
            )
        )
        await session.commit()

    return {
        "sessions": sessions.rowcount or 0,
        "email_tokens": tokens.rowcount or 0,
        "login_attempts": attempts.rowcount or 0,
    }
