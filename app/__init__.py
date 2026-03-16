import logging.config
import sys

from app.config import DEBUG_LOG_PATH, RUN_LOG_PATH, WORKFLOW_DEBUG_LOG_PATH, WORKFLOW_LOG_PATH

LOG_CONF = {
    'version': 1,
    'formatters': {
        'verbose': {
            'format': "%(asctime)s %(filename)s[line:%(lineno)d](Pid:%(process)d "
                      "Thread:%(threadName)s) %(levelname)s %(message)s",
            # 'datefmt': "%Y-%m-%d %H:%M:%S"
        },
        'simple': {
            'format': '%(asctime)s %(filename)s-%(lineno)d [%(levelname)s]-%(threadName)s: %(message)s'
        },
    },
    'handlers': {
        'console': {
            'level': logging.DEBUG,
            'class': 'logging.StreamHandler',
            'stream': sys.stdout,
            'formatter': 'verbose'
        },
        'file': {
            'level': logging.INFO,
            'class': 'app.core.ajlog.SafeRotatingFileHandler',
            'when': 'W0',
            'interval': 1,
            'backupCount': 1,
            'filename': RUN_LOG_PATH,
            'formatter': 'verbose'
        },
        'debug': {
            'level': logging.DEBUG,
            'class': 'app.core.ajlog.SafeRotatingFileHandler',
            'when': 'W0',
            'interval': 1,
            'backupCount': 1,
            'filename': DEBUG_LOG_PATH,
            'formatter': 'verbose'
        },
        'workflow_file': {
            'level': logging.INFO,
            'class': 'app.core.ajlog.SafeRotatingFileHandler',
            'when': 'W0',
            'interval': 1,
            'backupCount': 1,
            'filename': WORKFLOW_LOG_PATH,
            'formatter': 'verbose'
        },
        'workflow_debug': {
            'level': logging.DEBUG,
            'class': 'app.core.ajlog.SafeRotatingFileHandler',
            'when': 'W0',
            'interval': 1,
            'backupCount': 1,
            'filename': WORKFLOW_DEBUG_LOG_PATH,
            'formatter': 'verbose'
        }
    },
    'root': {
        'handlers': ['console'],
        'level': logging.INFO,
    },
    'loggers': {
        'aj': {
            'handlers': ['file', 'debug'],
            'level': logging.DEBUG,
        },
        'workflow_executor': {
            'handlers': ['workflow_file', 'workflow_debug', 'console'],
            'level': logging.DEBUG,
            'propagate': False,  # 不传播到父logger，避免重复记录
        }
    }
}

logging.config.dictConfig(LOG_CONF)
logger = logging.getLogger("aj")
