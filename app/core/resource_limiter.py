"""
资源限制器 - 控制脚本执行资源使用
"""
import signal
import threading
import time
import traceback
from contextlib import contextmanager
from typing import Optional, Callable, Any


class TimeoutError(Exception):
    """执行超时错误"""
    pass


class MemoryLimitError(Exception):
    """内存限制错误"""
    pass


class ResourceLimiter:
    """资源限制器"""

    def __init__(self):
        self._timeout_timers = {}

    def set_timeout(self, seconds: float, callback: Callable = None):
        """
        设置执行超时（使用线程）

        Args:
            seconds: 超时时间（秒）
            callback: 超时回调函数
        """
        def timeout_handler():
            time.sleep(seconds)
            if callback:
                callback()
            else:
                raise TimeoutError(f"脚本执行超时 ({seconds}秒)")

        timer = threading.Thread(target=timeout_handler, daemon=True)
        timer.start()
        return timer

    @contextmanager
    def timeout_context(self, seconds: float):
        """
        超时上下文管理器

        Args:
            seconds: 超时时间（秒）

        Usage:
            with timeout_context(30):
                result = script_function()
        """
        # 检查是否在主线程中
        is_main_thread = threading.current_thread() is threading.main_thread()

        # 只有在主线程中才能使用 SIGALRM
        if is_main_thread and hasattr(signal, 'SIGALRM'):
            # 使用 signal.SIGALRM（更精确）
            def timeout_handler(signum, frame):
                raise TimeoutError(f"脚本执行超时 ({seconds}秒)")

            old_handler = signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(int(seconds))

            try:
                yield
            finally:
                signal.alarm(0)  # 取消闹钟
                signal.signal(signal.SIGALRM, old_handler)  # 恢复原处理器
        else:
            # 在非主线程或 Windows 系统中，不强制超时
            # 使用线程定时器来记录超时，但不会中断执行
            import logging
            logger = logging.getLogger(__name__)

            def timeout_warning():
                logger.warning(f"脚本执行超过 {seconds} 秒（在非主线程中无法强制中断）")

            timer = threading.Timer(seconds, timeout_warning)
            timer.daemon = True
            timer.start()

            try:
                yield
            finally:
                timer.cancel()

    def measure_memory(self) -> int:
        """
        测量当前进程内存使用（MB）

        Returns:
            内存使用量（MB）
        """
        try:
            import psutil
            process = psutil.Process()
            return process.memory_info().rss / 1024 / 1024  # 字节 -> MB
        except ImportError:
            # 如果没有 psutil，使用简化方法
            import resource
            # Unix系统
            if hasattr(resource, 'getrusage'):
                usage = resource.getrusage(resource.RUSAGE_SELF)
                return usage.ru_maxrss / 1024  # KB -> MB (在某些系统上已经是KB)
            return 0

    @contextmanager
    def memory_limit_context(self, limit_mb: int):
        """
        内存限制上下文管理器

        Args:
            limit_mb: 内存限制（MB）

        Note:
            真正的内存限制需要操作系统支持（如 cgroup）
            这里只是监控和警告
        """
        start_memory = self.measure_memory()

        try:
            yield
        finally:
            end_memory = self.measure_memory()
            used = end_memory - start_memory

            if used > limit_mb:
                print(f"[ResourceLimiter] 警告: 内存使用 {used:.2f}MB 超过限制 {limit_mb}MB")

    def limit_resources(self, timeout: float = None, memory_mb: int = None):
        """
        组合资源限制上下文管理器

        Args:
            timeout: 超时时间（秒）
            memory_mb: 内存限制（MB）

        Usage:
            with limit_resources(timeout=30, memory_mb=512):
                result = script_function()
        """
        contexts = []

        if timeout:
            contexts.append(self.timeout_context(timeout))

        if memory_mb:
            contexts.append(self.memory_limit_context(memory_mb))

        # 按顺序嵌套上下文管理器
        from contextlib import nested

        # Python 3.3+ 方式
        if hasattr(contextlib, 'ExitStack'):
            from contextlib import ExitStack

            with ExitStack() as stack:
                for ctx in contexts:
                    stack.enter_context(ctx)
                yield
        else:
            # 旧版本方式
            with nested(*contexts):
                yield


class ExecutionTimer:
    """执行计时器"""

    def __init__(self):
        self.start_time = None
        self.end_time = None
        self.elapsed_ms = None

    def start(self):
        """开始计时"""
        self.start_time = time.time()

    def stop(self):
        """停止计时"""
        if self.start_time:
            self.end_time = time.time()
            self.elapsed_ms = (self.end_time - self.start_time) * 1000

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()


class ScriptExecutor:
    """
    脚本执行器 - 带资源限制的脚本执行
    """

    def __init__(self, timeout: float = 30, memory_limit_mb: int = 512):
        """
        初始化执行器

        Args:
            timeout: 默认超时时间（秒）
            memory_limit_mb: 默认内存限制（MB）
        """
        self.limiter = ResourceLimiter()
        self.default_timeout = timeout
        self.default_memory_limit = memory_limit_mb

    def execute(
        self,
        func: Callable,
        *args,
        timeout: float = None,
        memory_limit_mb: int = None,
        **kwargs
    ) -> tuple:
        """
        执行函数并限制资源

        Args:
            func: 要执行的函数
            *args: 函数参数
            timeout: 超时时间（秒），None使用默认值
            memory_limit_mb: 内存限制（MB），None使用默认值
            **kwargs: 函数关键字参数

        Returns:
            (result, execution_time_ms, success, error)

        Raises:
            TimeoutError: 超时
        """
        timeout = timeout or self.default_timeout
        memory_limit = memory_limit_mb or self.default_memory_limit

        timer = ExecutionTimer()
        timer.start()

        result = None
        success = False
        error = None

        try:
            # 使用超时上下文
            with self.limiter.timeout_context(timeout):
                result = func(*args, **kwargs)

            success = True

        except TimeoutError as e:
            error = str(e)
            print(f"[ScriptExecutor] 执行超时: {e}")

        except Exception as e:
            error = str(e)
            print(f"[ScriptExecutor] 执行失败: {e}")
            traceback.print_exc()

        finally:
            timer.stop()

        return result, timer.elapsed_ms, success, error


# 全局单例
_global_executor: Optional[ScriptExecutor] = None


def get_script_executor(timeout: float = 30, memory_limit_mb: int = 512) -> ScriptExecutor:
    """获取全局脚本执行器实例"""
    global _global_executor

    if _global_executor is None:
        _global_executor = ScriptExecutor(timeout, memory_limit_mb)

    return _global_executor


# 导入 contextlib
import contextlib
import contextlib
