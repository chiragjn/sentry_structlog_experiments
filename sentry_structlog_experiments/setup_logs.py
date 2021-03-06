import logging.config
import os
from typing import Dict, Any, Tuple, NamedTuple, Optional, List

import structlog
# Potentially dangerous, using library internals, no suitable alternatives for now
from structlog._frames import _find_first_app_frame_and_name
from structlog.processors import _figure_out_exc_info
from structlog.stdlib import ProcessorFormatter

from .env_vars import PROJECT_ROOT, DJANGO_LOG_LEVEL


# Custom Formatters
class StructlogAwareMessageFormatter(logging.Formatter):
    class AttrMapItem(NamedTuple):
        record_attr_name: str
        event_dict_key: str
        default_value: Any

    def __init__(self, copy_record: bool = True, attr_map: Optional[List[AttrMapItem]] = None, **kwargs):
        """
        :param copy_record: If to create a copy of the LogRecord object given to `format`
        :param attr_map: an optional list of AttrMapItem tuples
        :param kwargs: logging.Formatter.__init__ arguments
        """
        if attr_map is None:
            attr_map = []
        self.copy_record = copy_record
        self.attr_map = attr_map + [
            StructlogAwareMessageFormatter.AttrMapItem(record_attr_name='msg',
                                                       event_dict_key='event',
                                                       default_value='')
        ]
        super().__init__(**kwargs)

    def format(self, record: logging.LogRecord) -> str:
        """
        Modify LogRecord object when message is a structlog event dict by copying keys from event_dict into attributes
        on LogRecord directly

        :param record: LogRecord object
        :return: formatted message str with inplace edit to record object
        """
        if not isinstance(record.msg, str) and self.copy_record:
            record = logging.makeLogRecord(record.__dict__)

        if isinstance(record.msg, dict):
            event_dict = record.msg
            for attr_map_item in self.attr_map:
                setattr(record,
                        attr_map_item.record_attr_name,
                        event_dict.get(attr_map_item.event_dict_key, attr_map_item.default_value))

        return super().format(record=record)


class DropExcInfoFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.exc_text = None
        record.exc_info = None
        return super().format(record=record)


class CustomProcessFormatter(ProcessorFormatter, DropExcInfoFormatter):
    """
    A necessary evil where we inject DropExcInfoFormatter in the MRO.
    ProcessorFormatter.format -> DropExcInfoFormatter.format -> logging.Formatter.format

    Because we keep exc_info till the end (see wrap_for_process_formatter method below), when a call to
    logging.Formatter.format is made it appends duplicate non json traceback to the log message that can break your
    json only logs. To avoid this problem, we inject DropExcInfoFormatter in the call chain so exc info can be dropped
    from the record before formatting
    """

    @staticmethod
    def wrap_for_process_formatter(logger: logging.Logger, name: str,
                                   event_dict: Dict[str, Any]) -> Tuple[Tuple[Any, ...], Dict[str, Any]]:
        """
        Replacement for structlog.stdlib.ProcessorFormatter.wrap_for_formatter that passes on _exc_info as
        exc_info to LogRecord.__init__
        """
        args, kwargs = ProcessorFormatter.wrap_for_formatter(logger=logger, name=name, event_dict=event_dict)
        event_dict = args[0] if args else kwargs.get('event_dict')
        if event_dict:
            kwargs['exc_info'] = event_dict.pop('_exc_info', None)
        return args, kwargs


# Custom Processors
def format_exc_info(logger: logging.Logger, name: str, event_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format exception info but also save it to another key to process in wrap_for_process_formatter
    """
    exc_info = event_dict.pop('exc_info', None)
    if exc_info:
        event_dict['exc_info'] = _figure_out_exc_info(exc_info)
        event_dict['_exc_info'] = event_dict['exc_info']
    return structlog.processors.format_exc_info(logger=logger, name=name, event_dict=event_dict)


def add_module_and_lineno(logger: logging.Logger, name: str, event_dict: Dict[str, Any]) -> Dict[str, Any]:
    # see https://github.com/hynek/structlog/issues/253 for a feature request to get this done better
    frame, module_str = _find_first_app_frame_and_name(additional_ignores=[__name__, 'logging'])
    event_dict['modline'] = f'{module_str}:{frame.f_lineno}'
    return event_dict


# Actual logging config
FOREIGN_PRE_CHAIN_PROCESSORS = (  # For logs being emitted from logging.Logger but use structlog's ProcessFormatter
    structlog.processors.TimeStamper(fmt='iso'),
    structlog.stdlib.add_log_level,
    structlog.processors.StackInfoRenderer(),
    format_exc_info,
    structlog.processors.UnicodeDecoder(),
    add_module_and_lineno,
)

LOGGING_CONFIG = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'std': {
            '()': StructlogAwareMessageFormatter,
            'format': '[%(asctime)s][%(levelname)s][%(module)s:%(lineno)d] %(message)s'
        },
        'json_formatter': {
            '()': CustomProcessFormatter,
            'processor': structlog.processors.JSONRenderer(),
            'foreign_pre_chain': FOREIGN_PRE_CHAIN_PROCESSORS,
        },
        'colored': {
            '()': CustomProcessFormatter,
            'processor': structlog.dev.ConsoleRenderer(colors=True),
            'foreign_pre_chain': FOREIGN_PRE_CHAIN_PROCESSORS,
        },
        'key_value': {
            '()': CustomProcessFormatter,
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


def configure(**structlog_configure_kwargs):
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.processors.TimeStamper(fmt='iso'),
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(remove_positional_args=True),
            structlog.processors.StackInfoRenderer(),
            format_exc_info,
            structlog.processors.UnicodeDecoder(),
            add_module_and_lineno,
            CustomProcessFormatter.wrap_for_process_formatter,
        ],
        context_class=structlog.threadlocal.wrap_dict(dict),
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
        **structlog_configure_kwargs
    )
