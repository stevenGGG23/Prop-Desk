from urllib.parse import urlparse, urljoin
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from app import db, limiter
from models import User

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("accounts.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            login_user(user, remember=False)
            if user.must_change_password:
                flash("Please set a new password before continuing.", "warning")
                return redirect(url_for("auth.change_password"))
            next_page = request.args.get("next", "")
            # Reject absolute URLs to prevent open redirect
            parsed = urlparse(urljoin(request.host_url, next_page))
            safe = urlparse(request.host_url)
            if not (parsed.scheme in ("http", "https") and parsed.netloc == safe.netloc):
                next_page = ""
            return redirect(next_page or url_for("accounts.dashboard"))

        flash("Invalid username or password.", "error")

    return render_template("login.html")


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")

        if not current_user.check_password(current_pw):
            flash("Current password is incorrect.", "error")
        elif len(new_pw) < 8:
            flash("New password must be at least 8 characters.", "error")
        elif new_pw != confirm_pw:
            flash("Passwords do not match.", "error")
        else:
            current_user.set_password(new_pw)
            current_user.must_change_password = False
            db.session.commit()
            flash("Password updated.", "success")
            return redirect(url_for("accounts.dashboard"))

    return render_template("change_password.html")


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        action = request.form.get("action", "profile")

        if action == "profile":
            display_name = request.form.get("display_name", "").strip()
            username = request.form.get("username", "").strip().lower()
            email = request.form.get("email", "").strip().lower() or None
            timezone = request.form.get("timezone", "America/New_York").strip()
            bio = request.form.get("bio", "").strip() or None

            if not display_name:
                flash("Display name cannot be empty.", "error")
            elif not username:
                flash("Username cannot be empty.", "error")
            elif username != current_user.username and User.query.filter_by(username=username).first():
                flash("That username is already taken.", "error")
            elif email and User.query.filter(User.email == email, User.id != current_user.id).first():
                flash("That email is already in use.", "error")
            else:
                current_user.username = username
                current_user.display_name = display_name
                current_user.email = email
                current_user.timezone = timezone
                current_user.bio = bio
                db.session.commit()
                flash("Profile updated.", "success")
                return redirect(url_for("auth.settings"))

        elif action == "password":
            current_pw = request.form.get("current_password", "")
            new_pw = request.form.get("new_password", "")
            confirm_pw = request.form.get("confirm_password", "")

            if not current_user.check_password(current_pw):
                flash("Current password is incorrect.", "error")
            elif len(new_pw) < 8:
                flash("New password must be at least 8 characters.", "error")
            elif new_pw != confirm_pw:
                flash("Passwords do not match.", "error")
            else:
                current_user.set_password(new_pw)
                current_user.must_change_password = False
                db.session.commit()
                flash("Password updated.", "success")
                return redirect(url_for("auth.settings"))

    return render_template("settings.html")


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
