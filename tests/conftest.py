import os
import pytest

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")

from app import create_app


@pytest.fixture(autouse=True)
def _clear_espn_cache():
    # The ESPN TTL cache is process-global; clear it between tests so cached
    # results from one case don't bleed into another (they reuse slugs/ids).
    import services.espn_api as espn
    espn._ESPN_CACHE.clear()
    yield
    espn._ESPN_CACHE.clear()


@pytest.fixture
def app():
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()
