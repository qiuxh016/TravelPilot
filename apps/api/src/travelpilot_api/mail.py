"""Minimal transactional email delivery with a development-only in-memory outbox."""
import asyncio
import logging
import smtplib
from email.message import EmailMessage

from travelpilot_api.settings import settings

logger = logging.getLogger("travelpilot.mail")
_development_outbox: list[dict[str, str]] = []


def _send_sync(to_email: str, subject: str, link: str) -> None:
    if not settings.smtp_host:
        if settings.app_env.lower() == "production":
            raise RuntimeError("SMTP is required in production")
        _development_outbox.append({"email": to_email, "subject": subject, "link": link})
        logger.info("development_email to=%s subject=%s link=%s", to_email, subject, link)
        return
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from_email
    message["To"] = to_email
    message.set_content(f"请打开以下链接完成操作（有效期有限）：\n\n{link}\n")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


async def send_action_email(to_email: str, subject: str, link: str) -> None:
    await asyncio.to_thread(_send_sync, to_email, subject, link)


def latest_development_email(email: str) -> dict[str, str] | None:
    return next((item for item in reversed(_development_outbox) if item["email"] == email), None)
