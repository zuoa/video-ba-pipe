import logging.config
import sys

from app.config import (
    DEBUG_LOG_PATH,
    DECODER_DEBUG_LOG_PATH,
    DECODER_LOG_PATH,
    LOG_BACKUP_COUNT,
    LOG_MAX_BYTES,
    RUN_LOG_PATH,
    WORKFLOW_DEBUG_LOG_PATH,
    WORKFLOW_LOG_PATH,
)

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
            'level': logging.INFO,
            'class': 'logging.StreamHandler',
            'stream': sys.stdout,
            'formatter': 'verbose'
        },
        'file': {
            'level': logging.INFO,
            'class': 'app.core.ajlog.SafeRotatingFileHandler',
            'maxBytes': LOG_MAX_BYTES,
            'backupCount': LOG_BACKUP_COUNT,
            'filename': RUN_LOG_PATH,
            'formatter': 'verbose'
        },
        'debug': {
            'level': logging.DEBUG,
            'class': 'app.core.ajlog.SafeRotatingFileHandler',
            'maxBytes': LOG_MAX_BYTES,
            'backupCount': LOG_BACKUP_COUNT,
            'filename': DEBUG_LOG_PATH,
            'formatter': 'verbose'
        },
        'workflow_file': {
            'level': logging.INFO,
            'class': 'app.core.ajlog.SafeRotatingFileHandler',
            'maxBytes': LOG_MAX_BYTES,
            'backupCount': LOG_BACKUP_COUNT,
            'filename': WORKFLOW_LOG_PATH,
            'formatter': 'verbose'
        },
        'workflow_debug': {
            'level': logging.DEBUG,
            'class': 'app.core.ajlog.SafeRotatingFileHandler',
            'maxBytes': LOG_MAX_BYTES,
            'backupCount': LOG_BACKUP_COUNT,
            'filename': WORKFLOW_DEBUG_LOG_PATH,
            'formatter': 'verbose'
        },
        'decoder_file': {
            'level': logging.INFO,
            'class': 'app.core.ajlog.SafeRotatingFileHandler',
            'maxBytes': LOG_MAX_BYTES,
            'backupCount': LOG_BACKUP_COUNT,
            'filename': DECODER_LOG_PATH,
            'formatter': 'verbose',
            # 延迟打开：避免未使用的进程（web等）创建空日志文件
            'delay': True,
        },
        'decoder_debug': {
            'level': logging.DEBUG,
            'class': 'app.core.ajlog.SafeRotatingFileHandler',
            'maxBytes': LOG_MAX_BYTES,
            'backupCount': LOG_BACKUP_COUNT,
            'filename': DECODER_DEBUG_LOG_PATH,
            'formatter': 'verbose',
            'delay': True,
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
        },
        # 解码链路（decoder_worker 进程内的解码器实例 + orchestrator 接管的
        # 解码子进程输出）统一落到 decoder.log / decoder_debug.log
        'decoder': {
            'handlers': ['decoder_file', 'decoder_debug'],
            'level': logging.DEBUG,
            # 传播到 root console，docker logs 中保留 INFO 以上
        }
    }
}

logging.config.dictConfig(LOG_CONF)
logger = logging.getLogger("aj")


def create_rotating_file_handler(filename, level):
    """按全局统一样式创建轮转文件 handler（供子进程重定向日志文件时使用）。"""
    from app.core.ajlog import SafeRotatingFileHandler

    handler = SafeRotatingFileHandler(
        filename,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_CONF['formatters']['verbose']['format']))
    return handler
