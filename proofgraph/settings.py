import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

from proofgraph.config import database_config, env_bool

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DEBUG = env_bool(os.environ.get("DJANGO_DEBUG"), default=False)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "unsafe-local-development-key"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is required when DJANGO_DEBUG is false.")

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "DJANGO_CSRF_TRUSTED_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173",
    ).split(",")
    if origin.strip()
]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "proofgraph.graph",
    "proofgraph.generation",
    "proofgraph.runtime",
]

GENERATION_COMPOSITION_FACTORY = "proofgraph.generation.composition.production_composition"
GENERATION_LEASE_SECONDS = 60
GENERATION_HEARTBEAT_SECONDS = 12
GENERATION_MAX_JOBS_PER_WORKER = 50
GENERATION_MAX_WORKER_LIFETIME_SECONDS = 14_400
GENERATION_SSE_POLL_SECONDS = 1.0
GENERATION_SSE_HEARTBEAT_SECONDS = 15.0
GENERATION_CACHE_CLEANUP_SECONDS = 60.0
GENERATION_FIXTURE_ROOT = BASE_DIR / "fixtures" / "security-questionnaires" / "v1"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
]

ROOT_URLCONF = "proofgraph.urls"
ASGI_APPLICATION = "proofgraph.asgi.application"

DATABASES = {"default": database_config(os.environ)}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
