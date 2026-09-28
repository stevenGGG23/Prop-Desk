import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    RATELIMIT_DEFAULT = "200 per day;50 per hour"
    RATELIMIT_STORAGE_URL = "memory://"
    WTF_CSRF_ENABLED = True
    WTF_CSRF_HEADERS = ["X-CSRFToken"]

    # Daily email report — sent from and to REPORT_EMAIL using a Gmail app password.
    REPORT_EMAIL = os.environ.get("REPORT_EMAIL", "").strip()
    GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

    # Optional one-time account provisioning from deployment environment variables.
    BOOTSTRAP_USER_ENABLED = os.environ.get("BOOTSTRAP_USER_ENABLED", "").lower() == "true"
    BOOTSTRAP_USER_USERNAME = os.environ.get("BOOTSTRAP_USER_USERNAME", "").strip().lower()
    BOOTSTRAP_USER_DISPLAY_NAME = os.environ.get("BOOTSTRAP_USER_DISPLAY_NAME", "").strip()
    BOOTSTRAP_USER_EMAIL = os.environ.get("BOOTSTRAP_USER_EMAIL", "").strip().lower()
    BOOTSTRAP_USER_PASSWORD = os.environ.get("BOOTSTRAP_USER_PASSWORD", "")


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///dev.db")


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    _db_url = os.environ.get("DATABASE_URL", "")
    # Normalize URL: Render/Supabase may provide postgres:// or postgresql://
    # Force psycopg2 driver so SQLAlchemy 2.x doesn't try to load psycopg3
    if _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif _db_url.startswith("postgresql://"):
        _db_url = _db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    SQLALCHEMY_DATABASE_URI = _db_url
    # Refuse to run production with the public fallback key, which would let
    # anyone forge session cookies.
    if Config.SECRET_KEY == "dev-secret-change-in-production":
        SECRET_KEY = None


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    RATELIMIT_ENABLED = False
    WTF_CSRF_ENABLED = False


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
