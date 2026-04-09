import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")
APP_URL = os.environ.get("APP_URL", "http://localhost:5000")
