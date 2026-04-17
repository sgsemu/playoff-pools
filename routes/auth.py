from flask import Blueprint, render_template, request, redirect, session, flash
import bcrypt
from services.supabase_client import get_service_client

auth_bp = Blueprint("auth", __name__)


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

    session["user_id"] = user["id"]
    session["display_name"] = user["display_name"]
    pending = session.pop("pending_invite", None)
    return redirect(f"/join/{pending}" if pending else "/dashboard")


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect("/")


def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated
