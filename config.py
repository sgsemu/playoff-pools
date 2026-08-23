import os
from dotenv import load_dotenv

load_dotenv()

def _env(name, default=None, required=False):
    val = os.environ.get(name, default)
    if required and val is None:
        raise KeyError(name)
    return val.strip() if isinstance(val, str) else val

SUPABASE_URL = _env("SUPABASE_URL", required=True)
SUPABASE_KEY = _env("SUPABASE_KEY", required=True)
SUPABASE_SERVICE_KEY = _env("SUPABASE_SERVICE_KEY", required=True)
RESEND_API_KEY = _env("RESEND_API_KEY", "")
SECRET_KEY = _env("SECRET_KEY", "dev-secret-key")
APP_URL = _env("APP_URL", "http://localhost:5000")

SMTP_HOST = _env("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(_env("SMTP_PORT", "587"))
SMTP_USER = _env("SMTP_USER", "")          # the Gmail address
SMTP_PASSWORD = _env("SMTP_PASSWORD", "")  # a Gmail App Password
MAIL_FROM = _env("MAIL_FROM", "") or (f"Playoff Pools <{SMTP_USER}>" if SMTP_USER else "Playoff Pools <noreply@example.com>")
