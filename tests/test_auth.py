import os
import pytest
from unittest.mock import patch, MagicMock

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_register_page_loads(client):
    resp = client.get("/register")
    assert resp.status_code == 200
    assert b"Register" in resp.data


def test_login_page_loads(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b"Login" in resp.data


@patch("routes.auth.get_service_client")
def test_register_creates_user(mock_sb, client):
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    mock_table.select.return_value.eq.return_value.execute.return_value.data = []
    mock_table.insert.return_value.execute.return_value.data = [
        {"id": "test-uuid", "email": "test@example.com", "display_name": "Test"}
    ]

    resp = client.post("/register", data={
        "email": "test@example.com",
        "password": "password123",
        "display_name": "Test"
    }, follow_redirects=False)
    assert resp.status_code == 302
    assert "/dashboard" in resp.headers["Location"]


@patch("routes.auth.get_service_client")
def test_register_rejects_duplicate_email(mock_sb, client):
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    mock_table.select.return_value.eq.return_value.execute.return_value.data = [
        {"id": "existing-uuid"}
    ]

    resp = client.post("/register", data={
        "email": "taken@example.com",
        "password": "password123",
        "display_name": "Test"
    })
    assert resp.status_code == 200
    assert b"already registered" in resp.data


@patch("routes.auth.get_service_client")
def test_login_success(mock_sb, client):
    import bcrypt
    hashed = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    mock_table.select.return_value.eq.return_value.execute.return_value.data = [
        {"id": "test-uuid", "email": "test@example.com", "password_hash": hashed, "display_name": "Test"}
    ]

    resp = client.post("/login", data={
        "email": "test@example.com",
        "password": "password123"
    }, follow_redirects=False)
    assert resp.status_code == 302
    assert "/dashboard" in resp.headers["Location"]


@patch("routes.auth.get_service_client")
def test_login_wrong_password(mock_sb, client):
    import bcrypt
    hashed = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    mock_table.select.return_value.eq.return_value.execute.return_value.data = [
        {"id": "test-uuid", "email": "test@example.com", "password_hash": hashed, "display_name": "Test"}
    ]

    resp = client.post("/login", data={
        "email": "test@example.com",
        "password": "wrongpassword"
    })
    assert resp.status_code == 200
    assert b"Invalid" in resp.data


def test_logout(client):
    with client.session_transaction() as sess:
        sess["user_id"] = "test-uuid"
    resp = client.get("/logout", follow_redirects=False)
    assert resp.status_code == 302
    assert "/" in resp.headers["Location"]


def test_login_page_has_forgot_password_link(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b'href="/forgot"' in resp.data


@patch("routes.auth.send_password_reset")
@patch("routes.auth.get_service_client")
def test_forgot_unknown_email_no_email_sent(mock_sb, mock_send, client):
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    mock_table.select.return_value.eq.return_value.execute.return_value.data = []

    resp = client.post("/forgot", data={"email": "nobody@example.com"})
    assert resp.status_code == 200
    assert b"If that email is registered" in resp.data
    mock_send.assert_not_called()


@patch("routes.auth.send_password_reset")
@patch("routes.auth.get_service_client")
def test_forgot_known_email_sends_reset(mock_sb, mock_send, client):
    import bcrypt
    hashed = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    mock_table.select.return_value.eq.return_value.execute.return_value.data = [
        {"id": "test-uuid", "email": "test@example.com", "password_hash": hashed, "display_name": "Test"}
    ]

    resp = client.post("/forgot", data={"email": "test@example.com"})
    assert resp.status_code == 200
    assert b"If that email is registered" in resp.data
    mock_send.assert_called_once()
    args, kwargs = mock_send.call_args
    reset_url = args[1] if len(args) > 1 else kwargs.get("reset_url")
    assert "/reset/" in reset_url


@patch("routes.auth.send_password_reset")
@patch("routes.auth.get_service_client")
def test_forgot_survives_mail_provider_failure(mock_sb, mock_send, client):
    # A Resend/mail failure must not 500 the user (or leak enumeration) --
    # still 200 + the generic message.
    import bcrypt
    hashed = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    mock_table.select.return_value.eq.return_value.execute.return_value.data = [
        {"id": "u1", "email": "test@example.com", "password_hash": hashed, "display_name": "T"}
    ]
    mock_send.side_effect = Exception("Resend: API key is invalid")

    resp = client.post("/forgot", data={"email": "test@example.com"})
    assert resp.status_code == 200
    assert b"If that email is registered" in resp.data
    mock_send.assert_called_once()


@patch("routes.auth.get_service_client")
def test_reset_round_trip_updates_password(mock_sb, client):
    import bcrypt
    from routes.auth import _make_reset_token

    old_hash = bcrypt.hashpw(b"oldpassword", bcrypt.gensalt()).decode()
    user = {"id": "test-uuid", "email": "test@example.com", "password_hash": old_hash, "display_name": "Test"}

    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    mock_table.select.return_value.eq.return_value.execute.return_value.data = [user]

    token = _make_reset_token(user)

    get_resp = client.get(f"/reset/{token}")
    assert get_resp.status_code == 200
    assert b"Reset Password" in get_resp.data

    captured = {}

    def fake_update(payload):
        captured["password_hash"] = payload["password_hash"]
        return mock_table.update.return_value

    mock_table.update.side_effect = fake_update
    mock_table.update.return_value.eq.return_value.execute.return_value.data = [{"id": "test-uuid"}]

    post_resp = client.post(f"/reset/{token}", data={
        "password": "newpassword123",
        "confirm": "newpassword123"
    }, follow_redirects=False)
    assert post_resp.status_code == 302
    assert "/login" in post_resp.headers["Location"]

    new_hash = captured["password_hash"]
    assert bcrypt.checkpw(b"newpassword123", new_hash.encode())


def test_reset_expired_or_tampered_token_redirects_to_forgot(client):
    resp = client.get("/reset/this-is-not-a-valid-token", follow_redirects=False)
    assert resp.status_code == 302
    assert "/forgot" in resp.headers["Location"]


@patch("routes.auth.get_service_client")
def test_used_token_rejected_after_password_changed(mock_sb, client):
    import bcrypt
    from routes.auth import _make_reset_token

    old_hash = bcrypt.hashpw(b"oldpassword", bcrypt.gensalt()).decode()
    user = {"id": "test-uuid", "email": "test@example.com", "password_hash": old_hash, "display_name": "Test"}

    mock_table = MagicMock()
    mock_sb.return_value.table.return_value = mock_table
    mock_table.select.return_value.eq.return_value.execute.return_value.data = [user]

    token = _make_reset_token(user)

    mock_table.update.return_value.eq.return_value.execute.return_value.data = [{"id": "test-uuid"}]

    first_post = client.post(f"/reset/{token}", data={
        "password": "newpassword123",
        "confirm": "newpassword123"
    }, follow_redirects=False)
    assert first_post.status_code == 302
    assert "/login" in first_post.headers["Location"]

    # Simulate the password having changed by updating the mocked lookup
    # to reflect a new hash — the old token's fingerprint no longer matches.
    new_hash = bcrypt.hashpw(b"newpassword123", bcrypt.gensalt()).decode()
    mock_table.select.return_value.eq.return_value.execute.return_value.data = [
        {**user, "password_hash": new_hash}
    ]

    second_get = client.get(f"/reset/{token}", follow_redirects=False)
    assert second_get.status_code == 302
    assert "/forgot" in second_get.headers["Location"]
