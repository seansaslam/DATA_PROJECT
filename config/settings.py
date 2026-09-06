"""Django settings for the CCT Oil & Cattle multi-system simulator.

Django's own database is a local SQLite file and holds exactly one thing: the
connection profiles. The four simulated databases are reached through raw pyodbc
so their DDL stays vendor-shaped and hand-over ready.
"""
from pathlib import Path

from .env import env, env_bool, env_int, env_list, load_env

BASE_DIR = Path(__file__).resolve().parent.parent

# Read .env before anything below reads a setting. Real environment variables
# take precedence, so nothing here has to change to deploy elsewhere.
load_env()

SECRET_KEY = env("CCT_SECRET_KEY", "dev-only-insecure-key-override-in-.env")
DEBUG = env_bool("CCT_DEBUG", True)
ALLOWED_HOSTS = env_list("CCT_ALLOWED_HOSTS", "*")

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
    ]},
}]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "app_state.sqlite3",
        # Background jobs and the live ticker write from worker threads, so give
        # SQLite room to wait on a lock instead of failing the job.
        "OPTIONS": {"timeout": 20},
    }
}

# Port 5001, not 5000: the JDE ERP simulator on this machine already holds 5000.
APP_PORT = env_int("CCT_APP_PORT", 5001)

CSRF_TRUSTED_ORIGINS = [f"http://{h}:{APP_PORT}" for h in
                        env_list("CCT_CSRF_HOSTS", "localhost,127.0.0.1")]

# Defaults for a connection profile the first time the app runs. The profiles
# themselves live in app_state.sqlite3 and are edited on the Connections page;
# these only seed row one. Passwords never appear in source -- see .env.example.
SOURCE_DB_DEFAULTS = {
    "db_type": env("CCT_DB_TYPE", "sqlserver"),
    "server": env("CCT_DB_SERVER", "127.0.0.1"),
    "port": env_int("CCT_DB_PORT", 1433),
    "username": env("CCT_DB_USER", ""),
    "password": env("CCT_DB_PASSWORD", ""),
    "extra_options": env("CCT_DB_EXTRA_OPTIONS", ""),
}

STATIC_URL = "static/"

# Every timestamp in this system is naive UTC, because that is what the sources
# produce: the UPDATED_TS columns default to SYSUTCDATETIME() and pyodbc hands
# them back without a tzinfo. Turning USE_TZ on would make Django attach a
# timezone to watermarks that SQL Server never asserted, and comparing an aware
# datetime against a datetime2 column silently promotes it to datetimeoffset.
USE_TZ = False
TIME_ZONE = "America/Chicago"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
