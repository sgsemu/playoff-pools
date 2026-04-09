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
