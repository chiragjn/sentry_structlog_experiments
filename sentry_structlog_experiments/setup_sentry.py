import logging

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration, ignore_logger

from .env_vars import ENVIRONMENT, SENTRY_DSN

IGNORE_LOGGERS = [
    'django_structlog.middlewares.request'
]


def configure():
    for logger in IGNORE_LOGGERS:
        ignore_logger(logger)

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration(), LoggingIntegration()],
        environment=ENVIRONMENT
    )
