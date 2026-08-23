from flask import Blueprint, render_template, request, redirect, session, flash
import bcrypt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from services.supabase_client import get_service_client
from services.email import send_password_reset
import config

auth_bp = Blueprint("auth", __name__)

RESET_SALT = "pw-reset"
RESET_MAX_AGE = 3600


def _reset_serializer():
    return URLSafeTimedSerializer(config.SECRET_KEY, salt=RESET_SALT)


def _make_reset_token(user):
    fingerprint = user["password_hash"][:16]
    return _reset_serializer().dumps([user["id"], fingerprint])


def _verify_reset_token(token):
    """Return the user dict if the token is valid, unexpired, and matches the
    user's current password hash (making it single-use). Otherwise None."""
    try:
        user_id, fingerprint = _reset_serializer().loads(token, max_age=RESET_MAX_AGE)
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        return None

    sb = get_service_client()
    users = sb.table("users").select("*").eq("id", user_id).execute().data
    if not users:
        return None

    user = users[0]
    if user["password_hash"][:16] != fingerprint:
        return None

    return user


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("auth/register.html")

    email = request.form["email"].strip().lower()
    password = request.form["password"]
    display_name = request.form["display_name"].strip()

    sb = get_service_client()
    existing = sb.table("users").select("id").eq("email", email).execute().data
    if existing:
        flash("This email is already registered.", "error")
        return render_template("auth/register.html"), 200

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    result = sb.table("users").insert({
        "email": email,
        "password_hash": password_hash,
        "display_name": display_name
    }).execute()

    user = result.data[0]
    session.permanent = True
    session["user_id"] = user["id"]
    session["display_name"] = user["display_name"]
    pending = session.pop("pending_invite", None)
    return redirect(f"/join/{pending}" if pending else "/dashboard")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("auth/login.html")

    email = request.form["email"].strip().lower()
    password = request.form["password"]

    sb = get_service_client()
    users = sb.table("users").select("*").eq("email", email).execute().data
    if not users:
        flash("Invalid email or password.", "error")
        return render_template("auth/login.html"), 200

    user = users[0]
    if not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        flash("Invalid email or password.", "error")
        return render_template("auth/login.html"), 200

    session.permanent = True
    session["user_id"] = user["id"]
    session["display_name"] = user["display_name"]
    pending = session.pop("pending_invite", None)
    return redirect(f"/join/{pending}" if pending else "/dashboard")


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect("/")


GENERIC_RESET_MESSAGE = "If that email is registered, we've sent a reset link."


@auth_bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "GET":
        return render_template("auth/forgot.html")

    email = request.form["email"].strip().lower()

    sb = get_service_client()
    users = sb.table("users").select("*").eq("email", email).execute().data
    if users:
        user = users[0]
        token = _make_reset_token(user)
        reset_url = f"{config.APP_URL}/reset/{token}"
        send_password_reset(user["email"], reset_url)

    # Always show the same message, whether or not the account exists,
    # to avoid leaking which emails are registered.
    flash(GENERIC_RESET_MESSAGE, "success")
    return render_template("auth/forgot.html"), 200


@auth_bp.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    user = _verify_reset_token(token)
    if not user:
        flash("That password reset link is invalid or has expired. Please request a new one.", "error")
        return redirect("/forgot")

    if request.method == "GET":
        return render_template("auth/reset.html", token=token)

    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")

    if not password or len(password) < 6:
        flash("Password must be at least 6 characters.", "error")
        return render_template("auth/reset.html", token=token), 200

    if password != confirm:
        flash("Passwords do not match.", "error")
        return render_template("auth/reset.html", token=token), 200

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    sb = get_service_client()
    sb.table("users").update({"password_hash": password_hash}).eq("id", user["id"]).execute()

    flash("Your password has been reset. Please log in.", "success")
    return redirect("/login")


def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated
