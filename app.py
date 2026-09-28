import logging
import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from dotenv import load_dotenv
from sqlalchemy.exc import IntegrityError

load_dotenv()

logging.basicConfig(level=logging.INFO)

db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()

login_manager = LoginManager()
limiter = Limiter(key_func=get_remote_address)


def create_app(env=None):
    app = Flask(__name__)

    from config import config_map
    env = env or os.environ.get("FLASK_ENV", "development")
    app.config.from_object(config_map.get(env, config_map["development"]))
    if env == "production" and not app.config.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY must be set in production.")

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)
    limiter.init_app(app)

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Sign in to access Prop Desk."
    login_manager.login_message_category = "info"

    from models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Blueprints
    from routes.auth import bp as auth_bp
    from routes.accounts import bp as accounts_bp
    from routes.advisor import bp as advisor_bp
    from routes.stats import bp as stats_bp
    from routes.log import bp as log_bp
    from routes.bots import bp as bots_bp
    from routes.calendar import bp as calendar_bp
    from routes.projections import bp as projections_bp
    from routes.trades import bp as trades_bp
    from routes.api import bp as api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(advisor_bp)
    app.register_blueprint(stats_bp)
    app.register_blueprint(log_bp)
    app.register_blueprint(bots_bp)
    app.register_blueprint(calendar_bp)
    app.register_blueprint(projections_bp)
    app.register_blueprint(trades_bp)
    app.register_blueprint(api_bp)

    if not app.config.get("TESTING"):
        _bootstrap_user(app)

    # Public webhook endpoints are authenticated by token, not session — exempt from CSRF
    from routes.api import receive_webhook, inbound_webhook
    csrf.exempt(receive_webhook)
    csrf.exempt(inbound_webhook)

    from cli import register_cli
    register_cli(app)

    _start_scheduler(app)

    return app


def _bootstrap_user(app):
    """Create one explicitly configured user once; never reset an existing login."""
    if not app.config.get("BOOTSTRAP_USER_ENABLED"):
        return

    username = app.config.get("BOOTSTRAP_USER_USERNAME", "")
    display_name = app.config.get("BOOTSTRAP_USER_DISPLAY_NAME", "")
    email = app.config.get("BOOTSTRAP_USER_EMAIL", "")
    password = app.config.get("BOOTSTRAP_USER_PASSWORD", "")
    if not all((username, display_name, email, password)) or len(password) < 8:
        logging.getLogger(__name__).error(
            "Bootstrap user not created: required fields are missing or password is too short."
        )
        return

    from models import User

    with app.app_context():
        existing = User.query.filter(
            (User.username == username) | (User.email == email)
        ).first()
        if existing:
            logging.getLogger(__name__).info(
                "Bootstrap user skipped: username or email is already in use."
            )
            return

        user = User(
            username=username,
            display_name=display_name,
            email=email,
            must_change_password=True,
        )
        user.set_password(password)
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            logging.getLogger(__name__).info(
                "Bootstrap user skipped: username or email was created concurrently."
            )
            return

        logging.getLogger(__name__).info("Bootstrap user created: %s", username)


def _start_scheduler(app):
    """Start the daily-report background scheduler.

    Skipped in testing, and in the Werkzeug reloader parent process
    (which re-imports the module before forking the actual worker).
    """
    if app.config.get("TESTING"):
        return
    # When flask run is used with the reloader, WERKZEUG_RUN_MAIN is set
    # only in the child process.  Avoid starting two schedulers.
    if app.config.get("DEBUG") and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        from routes.api import send_daily_report_email

        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            func=send_daily_report_email,
            args=[app],
            trigger=CronTrigger(
                hour=6, minute=0,
                day_of_week="mon-fri",
                timezone="America/New_York",
            ),
            id="daily_report",
            replace_existing=True,
        )
        scheduler.start()
        logging.getLogger(__name__).info(
            "Daily report scheduler started — fires at 06:00 ET Mon-Fri."
        )
    except Exception as exc:  # pragma: no cover
        logging.getLogger(__name__).error("Failed to start scheduler: %s", exc)


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
