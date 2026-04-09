# tests/test_email.py
import os
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from unittest.mock import patch, MagicMock
from services.email import send_pool_invite, send_draft_turn_notification


@patch("services.email.resend.Emails.send")
def test_send_pool_invite(mock_send):
    mock_send.return_value = {"id": "email-123"}
    result = send_pool_invite("test@example.com", "Test Pool", "ABC123")
    assert result is not None
    mock_send.assert_called_once()
    call_args = mock_send.call_args[0][0]
    assert call_args["to"] == ["test@example.com"]
    assert "Test Pool" in call_args["subject"]


@patch("services.email.resend.Emails.send")
def test_send_draft_turn(mock_send):
    mock_send.return_value = {"id": "email-456"}
    result = send_draft_turn_notification("player@example.com", "My Pool", "pool-123")
    assert result is not None
    mock_send.assert_called_once()
