from supabase import create_client
import config

_client = None
_service_client = None

def get_client():
    global _client
    if _client is None:
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    return _client

def get_service_client():
    global _service_client
    if _service_client is None:
        _service_client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)
    return _service_client
