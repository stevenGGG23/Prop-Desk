import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_mail import Mail
from dotenv import load_dotenv

load_dotenv()

db = SQLAlchemy()
migrate = Migrate()
mail = Mail()

login_manager = LoginManager()
limiter = Limiter(key_func=get_remote_address)


def create_app(env=None):
    app = Flask(__name__)

    from config import config_map
    env = env or os.environ.get("FLASK_ENV", "development")
    app.config.from_object(config_map.get(env, config_map["development"]))

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    limiter.init_app(app)
    mail.init_app(app)

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
    from routes.api import bp as api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(advisor_bp)
    app.register_blueprint(stats_bp)
    app.register_blueprint(log_bp)
    app.register_blueprint(bots_bp)
    app.register_blueprint(calendar_bp)
    app.register_blueprint(projections_bp)
    app.register_blueprint(api_bp)

    from cli import register_cli
    register_cli(app)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
