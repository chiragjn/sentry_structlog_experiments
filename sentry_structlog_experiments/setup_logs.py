import logging.config
import os
from typing import Dict, Any

import structlog
# Potentially dangerous, using library internals, no suitable alternatives for now
from structlog._frames import _find_first_app_frame_and_name
from structlog_sentry import SentryJsonProcessor

from .env_vars import PROJECT_ROOT, DJANGO_LOG_LEVEL


def add_module_and_lineno(logger: logging.Logger, name: str, event_dict: Dict[str, Any]) -> Dict[str, Any]:
    # see https://github.com/hynek/structlog/issues/253 for a feature request to get this done better
    frame, module_str = _find_first_app_frame_and_name(additional_ignores=[__name__, 'logging'])
    event_dict['modline'] = f'{module_str}:{frame.f_lineno}'
    return event_dict


FOREIGN_PRE_CHAIN_PROCESSORS = (  # For logs being emitted from logging.Logger but use structlog's ProcessFormatter
    structlog.processors.TimeStamper(fmt='iso'),
    structlog.stdlib.add_log_level,
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
    structlog.processors.UnicodeDecoder(),
    add_module_and_lineno,
)

LOGGING_CONFIG = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'std': {
            'format': '[%(asctime)s][%(levelname)s][%(module)s:%(lineno)d] %(message)s'
        },
        'json_formatter': {
            '()': structlog.stdlib.ProcessorFormatter,
            'processor': structlog.processors.JSONRenderer(),
            'foreign_pre_chain': FOREIGN_PRE_CHAIN_PROCESSORS,
        },
        'colored': {
            '()': structlog.stdlib.ProcessorFormatter,
            'processor': structlog.dev.ConsoleRenderer(colors=True),
            'foreign_pre_chain': FOREIGN_PRE_CHAIN_PROCESSORS,
        },
        'key_value': {
            '()': structlog.stdlib.ProcessorFormatter,
            'processor': structlog.processors.KeyValueRenderer(key_order=['timestamp', 'level', 'event', 'logger']),
            'foreign_pre_chain': FOREIGN_PRE_CHAIN_PROCESSORS,
        }
    },
    'handlers': {
        'app_std': {
            'level': DJANGO_LOG_LEVEL,
            'class': 'logging.handlers.WatchedFileHandler',
            'filename': os.path.join(PROJECT_ROOT, 'logs', 'app.log'),
            'formatter': 'std'
        },
        'app_json': {
            'level': DJANGO_LOG_LEVEL,
            'class': 'logging.handlers.WatchedFileHandler',
            'filename': os.path.join(PROJECT_ROOT, 'logs', 'app.json.log'),
            'formatter': 'json_formatter'
        },
    },
    'loggers': {
        'app': {
            'handlers': ['app_std', 'app_json'],
            'level': 'DEBUG',
            'propagate': False
        },
    }
}


def configure():
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.processors.TimeStamper(fmt='iso'),
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(remove_positional_args=True),
            structlog.processors.StackInfoRenderer(),
            SentryJsonProcessor(level=logging.ERROR, as_extra=False, tag_keys=None),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            add_module_and_lineno,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=structlog.threadlocal.wrap_dict(dict),
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
