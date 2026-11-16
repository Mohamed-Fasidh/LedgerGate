from unittest.mock import patch

import pytest

from app.config import settings
from app.services.email import send_password_reset_email


def test_password_reset_email_uses_authenticated_starttls(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))
        def __enter__(self):
            return self
        def __exit__(self, *args):
            calls.append(("close",))
        def ehlo(self):
            calls.append(("ehlo",))
        def starttls(self, context):
            calls.append(("starttls", context))
        def login(self, username, password):
            calls.append(("login", username, password))
        def send_message(self, message):
            calls.append(("send", message["To"], message["Subject"], message.get_content()))

    monkeypatch.setattr(settings, "email_smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "email_smtp_port", 587)
    monkeypatch.setattr(settings, "email_smtp_username", "mailer@example.com")
    monkeypatch.setattr(settings, "email_smtp_password", "smtp-secret")
    monkeypatch.setattr(settings, "email_from_address", "LedgerGate <no-reply@example.com>")
    monkeypatch.setattr("app.services.email.smtplib.SMTP", FakeSMTP)

    send_password_reset_email("person@example.com", "https://app.example.com/?reset_token=secret")

    assert ("connect", "smtp.example.com", 587, 10) in calls
    assert any(call[0] == "starttls" for call in calls)
    assert ("login", "mailer@example.com", "smtp-secret") in calls
    assert any(call[0] == "send" and call[1] == "person@example.com" for call in calls)


def test_password_reset_email_fails_closed_without_smtp(monkeypatch):
    monkeypatch.setattr(settings, "email_smtp_host", "")
    with pytest.raises(RuntimeError, match="not configured"):
        send_password_reset_email("person@example.com", "https://app.example.com/reset")



def test_email_verification_uses_authenticated_starttls(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))
        def __enter__(self):
            return self
        def __exit__(self, *args):
            calls.append(("close",))
        def ehlo(self):
            calls.append(("ehlo",))
        def starttls(self, context):
            calls.append(("starttls", context))
        def login(self, username, password):
            calls.append(("login", username, password))
        def send_message(self, message):
            calls.append(("send", message["To"], message["Subject"], message.get_content()))

    monkeypatch.setattr(settings, "email_smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "email_smtp_port", 587)
    monkeypatch.setattr(settings, "email_smtp_username", "mailer@example.com")
    monkeypatch.setattr(settings, "email_smtp_password", "smtp-secret")
    monkeypatch.setattr(settings, "email_from_address", "LedgerGate <no-reply@example.com>")
    monkeypatch.setattr("app.services.email.smtplib.SMTP", FakeSMTP)

    from app.services.email import send_email_verification_email
    send_email_verification_email("person@example.com", "https://app.example.com/#verify_email_token=secret")

    assert any(call[0] == "starttls" for call in calls)
    assert ("login", "mailer@example.com", "smtp-secret") in calls
    assert any(call[0] == "send" and call[1] == "person@example.com" for call in calls)
