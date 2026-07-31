import os
from logging.handlers import RotatingFileHandler

try:
    # 多进程安全的轮转handler（基于文件锁），各worker进程可安全写同一日志文件
    from concurrent_log_handler import ConcurrentRotatingFileHandler

    _HAS_CONCURRENT_HANDLER = True
except ImportError:
    _HAS_CONCURRENT_HANDLER = False


def _ensure_log_dir(filename):
    file_dir = os.path.dirname(filename)
    if file_dir and not os.path.exists(file_dir):
        os.makedirs(file_dir)


if _HAS_CONCURRENT_HANDLER:
    class SafeRotatingFileHandler(ConcurrentRotatingFileHandler):
        """多进程共享日志文件时使用，内部通过文件锁保证轮转安全"""

        def __init__(self, filename, mode='a', maxBytes=0, backupCount=0, encoding=None, delay=False):
            _ensure_log_dir(filename)
            # 注意：新版本 concurrent-log-handler 忽略 delay 参数（隐含为 True），不再传入
            ConcurrentRotatingFileHandler.__init__(
                self, filename, mode=mode, maxBytes=maxBytes,
                backupCount=backupCount, encoding=encoding,
            )
else:
    class SafeRotatingFileHandler(RotatingFileHandler):
        """回退方案：concurrent-log-handler 未安装时使用

        多进程并发轮转时可能抛出 FileNotFoundError（其他进程已完成轮转），
        此处容忍该异常，仅丢失触发轮转的那一条日志。
        """

        def __init__(self, filename, mode='a', maxBytes=0, backupCount=0, encoding=None, delay=False):
            _ensure_log_dir(filename)
            RotatingFileHandler.__init__(self, filename, mode, maxBytes, backupCount, encoding, delay)

        def doRollover(self):
            try:
                RotatingFileHandler.doRollover(self)
            except FileNotFoundError:
                # 其他进程已完成轮转，跳过即可；后续emit会自动重新打开文件
                self.stream = None
