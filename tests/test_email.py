# tests/test_email.py
import os
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from unittest.mock import patch, MagicMock

import config
from services.email import (
    _send_email,
    send_pool_invite,
    send_draft_turn_notification,
    send_auction_alert,
    send_deadline_reminder,
    send_password_reset,
)


def _configured_smtp():
    """Patch config so _send_email thinks SMTP is configured, and patch
    smtplib.SMTP to return a MagicMock context manager."""
    return patch.multiple(
        config, SMTP_USER="sender@gmail.com", SMTP_PASSWORD="app-password"
    )


@patch("services.email.smtplib.SMTP")
def test_send_pool_invite(mock_smtp_cls):
    mock_conn = MagicMock()
    mock_smtp_cls.return_value.__enter__.return_value = mock_conn
    with _configured_smtp():
        result = send_pool_invite("test@example.com", "Test Pool", "ABC123")

    assert result is True
    mock_conn.starttls.assert_called_once()
    mock_conn.login.assert_called_once_with("sender@gmail.com", "app-password")
    mock_conn.send_message.assert_called_once()
    sent_msg = mock_conn.send_message.call_args[0][0]
    assert sent_msg["To"] == "test@example.com"
    assert "Test Pool" in sent_msg["Subject"]


@patch("services.email.smtplib.SMTP")
def test_send_draft_turn(mock_smtp_cls):
    mock_conn = MagicMock()
    mock_smtp_cls.return_value.__enter__.return_value = mock_conn
    with _configured_smtp():
        result = send_draft_turn_notification("player@example.com", "My Pool", "pool-123")

    assert result is True
    mock_conn.starttls.assert_called_once()
    mock_conn.login.assert_called_once()
    mock_conn.send_message.assert_called_once()
    sent_msg = mock_conn.send_message.call_args[0][0]
    assert sent_msg["To"] == "player@example.com"


@patch("services.email.smtplib.SMTP")
def test_send_auction_alert(mock_smtp_cls):
    mock_conn = MagicMock()
    mock_smtp_cls.return_value.__enter__.return_value = mock_conn
    with _configured_smtp():
        result = send_auction_alert("bidder@example.com", "Pool A", "pool-1", "Cool Team")

    assert result is True
    mock_conn.send_message.assert_called_once()
    sent_msg = mock_conn.send_message.call_args[0][0]
    assert "Cool Team" in sent_msg["Subject"]


@patch("services.email.smtplib.SMTP")
def test_send_deadline_reminder(mock_smtp_cls):
    mock_conn = MagicMock()
    mock_smtp_cls.return_value.__enter__.return_value = mock_conn
    with _configured_smtp():
        result = send_deadline_reminder("player@example.com", "My Pool", "pool-123", 3)

    assert result is True
    mock_conn.send_message.assert_called_once()
    sent_msg = mock_conn.send_message.call_args[0][0]
    assert "3h left" in sent_msg["Subject"]


@patch("services.email.smtplib.SMTP")
def test_send_password_reset(mock_smtp_cls):
    mock_conn = MagicMock()
    mock_smtp_cls.return_value.__enter__.return_value = mock_conn
    with _configured_smtp():
        result = send_password_reset("test@example.com", "https://example.com/reset/xyz")

    assert result is True
    mock_conn.starttls.assert_called_once()
    mock_conn.login.assert_called_once_with("sender@gmail.com", "app-password")
    mock_conn.send_message.assert_called_once()
    sent_msg = mock_conn.send_message.call_args[0][0]
    assert sent_msg["To"] == "test@example.com"
    assert "Reset your" in sent_msg["Subject"]


@patch("services.email.smtplib.SMTP")
def test_send_email_noop_when_unconfigured(mock_smtp_cls):
    with patch.multiple(config, SMTP_USER="", SMTP_PASSWORD=""):
        result = _send_email("test@example.com", "Subject", "<p>hi</p>")

    assert result is None
    mock_smtp_cls.assert_not_called()
