"""
Django base settings for Voice Diary project (shared by dev and prod).

For more information on this file, see
https://docs.djangoproject.com/en/5.0/topics/settings/
"""

import os
from pathlib import Path

from decouple import config
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

# Audio temp path: app directory (e.g. /srv/utter-it/app/audio) unless overridden by env
_storage_path = config("STORAGE_AUDIO_TEMP_PATH", default=str(BASE_DIR / "audio"))
os.environ["STORAGE_AUDIO_TEMP_PATH"] = _storage_path

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.postgres',

    # Third-party
    'corsheaders',
    'channels',

    # Tailwind
    'django_tailwind_cli',
    'django_browser_reload',

    # Local apps
    'src.accounts',
    'src.core',
    'src.ingestion',
    'src.recordings',
    'src.transcription',
    'src.translation',
    'src.lang_detect',
    'src.entries',
    'src.classification',
    'src.text_input',
    'src.quotas.apps.QuotasConfig',
    'src.text_rewrite.apps.TextRewriteConfig',
    'src.batch_calendar.apps.BatchCalendarConfig',
    'src.list_parser.apps.ListParserConfig',
    'src.financial_parser.apps.FinancialParserConfig',
    'src.managed_lists.apps.ManagedListsConfig',
    'src.intent_router.apps.IntentRouterConfig',
    'src.retrieval.apps.RetrievalConfig',
    'src.vd_tools.test_microphone',
    'src.vd_tools.recent_recordings',
    'src.billing',
    'src.gigo.apps.GigoConfig',
    'src.gmail_parsers.apps.GmailParsersConfig',
    'src.invoice_parser.apps.InvoiceParserConfig',

]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'src.accounts.middleware.UserInterfaceLanguageMiddleware',
    'src.common.middleware.NoCacheAuthenticatedMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_browser_reload.middleware.BrowserReloadMiddleware',
    'src.accounts.middleware.OnboardingMiddleware',
]

ROOT_URLCONF = 'src.utter_it.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'src' / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.csrf',
                'src.batch_calendar.context_processors.pending_calendar_events',
                'src.accounts.context_processors.theme_preferences',
                'src.gigo.context_processors.gigo_alert',
            ],
        },
    },
]

# Database -- always via DATABASE_URL connection string (dev and prod)
DATABASES = {
    'default': dj_database_url.config(
        default=config('DATABASE_URL'),
        conn_max_age=0,
        conn_health_checks=True,
    )
}

WSGI_APPLICATION = 'src.utter_it.wsgi.application'

# Auth
AUTH_USER_MODEL = 'accounts.CustomUser'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
     'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.BCryptSHA256PasswordHasher',
    'django.contrib.auth.hashers.ScryptPasswordHasher',
]

# Internationalization
LANGUAGE_CODE = 'pt-pt'

LANGUAGES = [
    ('pt-pt', 'Português (Portugal)'),
    ('en', 'English'),
]

LOCALE_PATHS = [BASE_DIR / 'src' / 'locale']

TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'src' / 'staticfiles'
STATICFILES_DIRS = [
    d for d in [BASE_DIR / 'static', BASE_DIR / 'src' / 'static']
    if d.exists()
]

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Auth redirects
LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'recordings:record'
LOGOUT_REDIRECT_URL = 'accounts:login'

# Email
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='your-email@gmail.com')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='your-app-password')
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER

# Security
SECURE_CONTENT_TYPE_NOSNIFF = False
SECURE_BROWSER_XSS_FILTER = True

# Calendar webhook for Google push notifications (must be HTTPS).
# If empty, derived from GOOGLE_OAUTH_REDIRECT_URI domain (e.g. https://utter-it.com).
CALENDAR_WEBHOOK_BASE_URL = config('CALENDAR_WEBHOOK_BASE_URL', default='')

# File upload limits (matches client: 100MB per file, 500MB total)
DATA_UPLOAD_MAX_MEMORY_SIZE = 550 * 1024 * 1024   # 550MB request body limit
FILE_UPLOAD_MAX_MEMORY_SIZE = 2_621_440  # 2.5MB -- forces disk-backed uploads for files above this threshold

# Quota dashboard cache TTL (seconds). Override via QUOTA_DASHBOARD_CACHE_SECONDS in .env.
QUOTA_DASHBOARD_CACHE_SECONDS = int(config('QUOTA_DASHBOARD_CACHE_SECONDS', default='120'))

# Django cache (Redis DB 4; Celery uses 0-1, Channels uses 2, pending transcriptions use 3)
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': f"redis://{config('DJANGO_CACHE_REDIS_HOST', default='127.0.0.1')}:{int(config('DJANGO_CACHE_REDIS_PORT', default='6379'))}/{config('DJANGO_CACHE_REDIS_DB', default='4')}",
    }
}

# Sessions via Redis cache instead of database
SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
SESSION_CACHE_ALIAS = 'default'

# Celery
CELERY_BROKER_URL = config('CELERY_BROKER_URL', default='redis://127.0.0.1:6379/0')
CELERY_RESULT_BACKEND = config('CELERY_RESULT_BACKEND', default='redis://127.0.0.1:6379/1')
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 60 * 15
CELERY_TASK_SOFT_TIME_LIMIT = 60 * 14
# Ensure common tasks (upload_attachments, rotate_master_encryption_key) are discovered
CELERY_IMPORTS = ('src.common.tasks', 'src.common.encryption_tasks')

# Django Channels
ASGI_APPLICATION = 'src.utter_it.asgi.application'

# Tailwind (django-tailwind-cli)
TAILWIND_CLI_SRC_CSS = 'src/theme/static/src/css/input.css'
TAILWIND_CLI_DIST_CSS = 'css/tailwind.css'

# Django Channels (own Redis DB to reduce contention with Celery)
CHANNEL_REDIS_DB = config('CHANNEL_LAYERS_REDIS_DB', default='2')

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [f"redis://{config('CHANNEL_LAYERS_REDIS_HOST', default='127.0.0.1')}:{int(config('CHANNEL_LAYERS_REDIS_PORT', default='6379'))}/{CHANNEL_REDIS_DB}"],
        },
    },
}

# Django logging: request errors + tracebacks to console (journalctl)
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'console': {
            'format': '%(levelname)s %(asctime)s %(name)s: %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'console',
        },
    },
    'loggers': {
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'src': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
