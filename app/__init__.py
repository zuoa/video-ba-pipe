import logging.config
import os
import sys

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
            'filename': '/data/logs/run.log' if os.path.exists('/.dockerenv') else 'data/logs/run.log',
            'formatter': 'verbose'
        },
        'debug': {
            'level': logging.DEBUG,
            'class': 'app.core.ajlog.SafeRotatingFileHandler',
            'when': 'W0',
            'interval': 1,
            'backupCount': 1,
            'filename': '/data/logs/debug.log' if os.path.exists('/.dockerenv') else 'data/logs/debug.log',
            'formatter': 'verbose'
        },
        'workflow_file': {
            'level': logging.INFO,
            'class': 'app.core.ajlog.SafeRotatingFileHandler',
            'when': 'W0',
            'interval': 1,
            'backupCount': 1,
            'filename': '/data/logs/workflow.log' if os.path.exists('/.dockerenv') else 'data/logs/workflow.log',
            'formatter': 'verbose'
        },
        'workflow_debug': {
            'level': logging.DEBUG,
            'class': 'app.core.ajlog.SafeRotatingFileHandler',
            'when': 'W0',
            'interval': 1,
            'backupCount': 1,
            'filename': '/data/logs/workflow_debug.log' if os.path.exists('/.dockerenv') else 'data/logs/workflow_debug.log',
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
