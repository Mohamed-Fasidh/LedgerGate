"""SMTP delivery for account recovery; tokens are never logged or persisted raw."""
import smtplib
from email.message import EmailMessage
from urllib.parse import quote

from app.config import settings


def send_password_reset_email(recipient: str, token: str) -> None:
    if not all((settings.smtp_host.strip(), settings.smtp_from.strip(), settings.password_reset_url_base.strip())):
        raise RuntimeError("Password recovery email is not configured")
    url = f"{settings.password_reset_url_base.rstrip('/')}?token={quote(token, safe='')}"
    message = EmailMessage()
    message["Subject"] = "LedgerGate password reset"
    message["From"] = settings.smtp_from
    message["To"] = recipient
    message.set_content(
        f"A password reset was requested for your LedgerGate account. Use this one-time link within 15 minutes:\n\n{url}\n\nIf you did not request it, you can ignore this message."
    )
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as client:
        client.starttls()
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(message)
