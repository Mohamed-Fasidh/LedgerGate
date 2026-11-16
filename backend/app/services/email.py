"""Transactional SMTP email delivery for account security messages."""
import smtplib
import ssl
from email.message import EmailMessage

from app.config import settings


def send_password_reset_email(recipient: str, reset_url: str) -> None:
    """Send a reset link using authenticated SMTP with mandatory STARTTLS."""
    if not settings.email_smtp_host or not settings.email_smtp_username or not settings.email_smtp_password:
        raise RuntimeError("SMTP account-recovery delivery is not configured")
    message = EmailMessage()
    message["Subject"] = "Reset your LedgerGate password"
    message["From"] = settings.email_from_address
    message["To"] = recipient
    message.set_content(
        "A password reset was requested for your LedgerGate account. "
        "Use the link below within 30 minutes. If you did not request this, "
        "you can ignore this email.\n\n"
        f"{reset_url}\n\n"
        "For your security, this link can only be used once."
    )
    context = ssl.create_default_context()
    with smtplib.SMTP(settings.email_smtp_host, settings.email_smtp_port, timeout=10) as client:
        client.ehlo()
        client.starttls(context=context)
        client.ehlo()
        client.login(settings.email_smtp_username, settings.email_smtp_password)
        client.send_message(message)
