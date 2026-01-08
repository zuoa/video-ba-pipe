"""
脚本加载器 - 动态加载和管理用户脚本
"""
import ast
import hashlib
import importlib
import importlib.util
import os
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple, Any


class ScriptLoadError(Exception):
    """脚本加载错误"""
    pass


class ScriptValidationError(Exception):
    """脚本验证错误"""
    pass


class ScriptLoader:
    """脚本加载器"""

    def __init__(self, scripts_root: str = None):
        """
        初始化脚本加载器

        Args:
            scripts_root: 脚本根目录，默认为 app/user_scripts
        """
        if scripts_root is None:
            # 默认脚本根目录
            current_dir = Path(__file__).parent
            self.scripts_root = str(current_dir.parent / "user_scripts")
        else:
            self.scripts_root = scripts_root

        # 缓存：{script_path: {'module': module, 'mtime': float, 'hash': str, 'state': Any}}
        self._cache: Dict[str, Dict] = {}

        # 确保脚本根目录存在
        os.makedirs(self.scripts_root, exist_ok=True)

        # 添加脚本根目录到 Python 路径
        if self.scripts_root not in sys.path:
            sys.path.insert(0, self.scripts_root)

    def resolve_path(self, script_path: str) -> str:
        """
        解析脚本路径（相对路径 -> 绝对路径）

        Args:
            script_path: 相对于 scripts_root 的路径

        Returns:
            绝对路径
        """
        # 移除开头的 / 或 ./
        script_path = script_path.lstrip('/').lstrip('.')

        # 构建绝对路径
        abs_path = os.path.join(self.scripts_root, script_path)

        # 规范化路径
        abs_path = os.path.normpath(abs_path)

        return abs_path

    def calculate_hash(self, file_path: str) -> Tuple[str, str]:
        """
        计算文件hash（SHA256）

        Returns:
            (file_hash, content_hash)
        """
        with open(file_path, 'rb') as f:
            content = f.read()
            file_hash = hashlib.sha256(content).hexdigest()
            # 也可以计算不含空行和注释的内容hash
            lines = [l for l in content.split(b'\n') if l.strip() and not l.strip().startswith(b'#')]
            content_hash = hashlib.sha256(b'\n'.join(lines)).hexdigest()

        return file_hash, content_hash

    def validate_syntax(self, file_path: str) -> bool:
        """
        验证Python语法

        Args:
            file_path: 脚本文件路径

        Returns:
            是否语法正确

        Raises:
            ScriptValidationError: 语法错误
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source = f.read()
            ast.parse(source)
            return True
        except SyntaxError as e:
            raise ScriptValidationError(f"语法错误: {e}")

    def validate_metadata(self, module) -> bool:
        """
        验证脚本元数据

        Args:
            module: 已加载的模块

        Returns:
            是否包含必需的元数据
        """
        # 检查是否有 SCRIPT_METADATA
        if not hasattr(module, 'SCRIPT_METADATA'):
            # 没有元数据也可以，但会有警告
            return False

        metadata = module.SCRIPT_METADATA
        required_fields = ['name', 'version']

        for field in required_fields:
            if field not in metadata:
                raise ScriptValidationError(f"缺少必需的元数据字段: {field}")

        return True

    def validate_security(self, file_path: str) -> bool:
        """
        安全检查（AST静态分析）

        Args:
            file_path: 脚本文件路径

        Returns:
            是否通过安全检查

        Raises:
            ScriptValidationError: 检测到危险操作
        """
        forbidden_patterns = {
            'eval': 'eval() 函数存在安全风险',
            'exec': 'exec() 函数存在安全风险',
            '__import__': '直接使用 __import__ 可能绕过安全限制',
            'compile': 'compile() 函数可能被滥用',
            'open(': '文件操作应使用提供的工具函数',
            'subprocess.': 'subprocess 模块可能被用于执行系统命令',
            'os.system': 'os.system() 存在命令注入风险',
            'os.popen': 'os.popen() 存在命令注入风险',
            'socket.': '网络操作被限制',
        }

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source = f.read()

            # 简单的字符串匹配（生产环境应使用更复杂的AST分析）
            for pattern, message in forbidden_patterns.items():
                if pattern in source:
                    # 检查是否在注释中
                    lines = source.split('\n')
                    for i, line in enumerate(lines, 1):
                        if pattern in line and not line.strip().startswith('#'):
                            raise ScriptValidationError(
                                f"第 {i} 行检测到潜在危险操作 ({pattern}): {message}"
                            )

            return True

        except ScriptValidationError:
            raise
        except Exception as e:
            # 静态分析失败，但不阻止加载（可能在运行时再检查）
            print(f"[ScriptLoader] 警告: 安全检查失败: {e}")
            return True

    def load(self, script_path: str, reload: bool = False) -> Tuple[Any, Dict]:
        """
        加载脚本模块

        Args:
            script_path: 相对于 scripts_root 的路径
            reload: 是否强制重新加载

        Returns:
            (module, metadata)

        Raises:
            ScriptLoadError: 加载失败
            ScriptValidationError: 验证失败
        """
        abs_path = self.resolve_path(script_path)

        # 检查文件是否存在
        if not os.path.exists(abs_path):
            raise ScriptLoadError(f"脚本文件不存在: {abs_path}")

        # 获取文件修改时间
        mtime = os.path.getmtime(abs_path)

        # 检查缓存
        if script_path in self._cache and not reload:
            cached = self._cache[script_path]
            if cached['mtime'] == mtime:
                return cached['module'], cached.get('metadata', {})

        # 验证语法
        try:
            self.validate_syntax(abs_path)
        except ScriptValidationError as e:
            raise ScriptLoadError(f"语法验证失败: {e}")

        # 安全检查（可选，可以通过配置禁用）
        try:
            self.validate_security(abs_path)
        except ScriptValidationError as e:
            raise ScriptLoadError(f"安全检查失败: {e}")

        # 计算hash
        file_hash, content_hash = self.calculate_hash(abs_path)

        # 动态加载模块
        try:
            module_name = f"user_scripts.{script_path.replace('/', '.').replace('\\', '.').rstrip('.py')}"

            spec = importlib.util.spec_from_file_location(module_name, abs_path)
            if spec is None or spec.loader is None:
                raise ScriptLoadError(f"无法创建模块规范: {abs_path}")

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            
            # === 注入模型解析器辅助函数 ===
            # 让脚本可以直接使用 resolve_model() 等函数，无需导入
            try:
                from app.core.model_resolver import inject_model_helpers
                inject_model_helpers(vars(module))
            except Exception as inject_error:
                # 注入失败不影响脚本加载，只记录警告
                import warnings
                warnings.warn(f"注入模型解析器失败: {inject_error}")

        except Exception as e:
            raise ScriptLoadError(f"模块加载失败: {e}")

        # 验证元数据
        try:
            has_metadata = self.validate_metadata(module)
        except ScriptValidationError as e:
            raise ScriptLoadError(f"元数据验证失败: {e}")

        # 获取元数据
        metadata = getattr(module, 'SCRIPT_METADATA', {}) if has_metadata else {}

        # 验证必需函数
        if not hasattr(module, 'process'):
            raise ScriptLoadError(f"脚本缺少必需的 process() 函数")

        # 缓存
        self._cache[script_path] = {
            'module': module,
            'mtime': mtime,
            'hash': file_hash,
            'content_hash': content_hash,
            'metadata': metadata,
            'state': None  # init() 函数的状态
        }

        return module, metadata

    def get_module(self, script_path: str) -> Optional[Any]:
        """
        从缓存获取模块（不重新加载）

        Args:
            script_path: 脚本路径

        Returns:
            模块对象，如果未缓存则返回 None
        """
        if script_path in self._cache:
            return self._cache[script_path]['module']
        return None

    def reload(self, script_path: str) -> Tuple[Any, Dict]:
        """
        重新加载脚本

        Args:
            script_path: 脚本路径

        Returns:
            (module, metadata)
        """
        return self.load(script_path, reload=True)

    def unload(self, script_path: str):
        """
        卸载脚本（从缓存移除）

        Args:
            script_path: 脚本路径
        """
        if script_path in self._cache:
            # 调用 cleanup 函数（如果存在）
            cached = self._cache[script_path]
            module = cached['module']
            state = cached.get('state')

            if hasattr(module, 'cleanup') and state is not None:
                try:
                    module.cleanup(state)
                except Exception as e:
                    print(f"[ScriptLoader] 警告: cleanup() 调用失败: {e}")

            # 从缓存移除
            del self._cache[script_path]

            # 从 sys.modules 移除
            module_name = f"user_scripts.{script_path.replace('/', '.').replace('\\', '.').rstrip('.py')}"
            if module_name in sys.modules:
                del sys.modules[module_name]

    def check_updates(self) -> list:
        """
        检查所有缓存的脚本是否有更新

        Returns:
            有更新的脚本路径列表
        """
        updated = []

        for script_path, cached in self._cache.items():
            abs_path = self.resolve_path(script_path)

            if not os.path.exists(abs_path):
                continue

            mtime = os.path.getmtime(abs_path)
            if mtime != cached['mtime']:
                updated.append(script_path)

        return updated

    def list_scripts(self, category: str = None) -> list:
        """
        列出可用的脚本

        Args:
            category: 脚本类别 ('detectors', 'filters', 'hooks', 'postprocessors')

        Returns:
            脚本信息列表
        """
        scripts = []

        if category:
            search_dir = os.path.join(self.scripts_root, category)
            if not os.path.exists(search_dir):
                return scripts
        else:
            search_dir = self.scripts_root

        for root, dirs, files in os.walk(search_dir):
            # 跳过 __pycache__ 和隐藏目录
            dirs[:] = [d for d in dirs if not d.startswith('_') and not d.startswith('.')]

            for file in files:
                if file.endswith('.py') and not file.startswith('_'):
                    rel_path = os.path.relpath(
                        os.path.join(root, file),
                        self.scripts_root
                    )

                    scripts.append({
                        'path': rel_path,
                        'category': os.path.dirname(rel_path),
                        'name': file[:-3]
                    })

        return scripts

    def get_script_metadata(self, script_path: str) -> Optional[Dict]:
        """
        获取脚本元数据

        Args:
            script_path: 脚本路径

        Returns:
            脚本元数据字典，如果失败则返回 None
        """
        try:
            module, metadata = self.load(script_path)
            return metadata
        except Exception as e:
            print(f"[ScriptLoader] 获取元数据失败: {e}")
            return None

    def get_cache_info(self) -> Dict:
        """
        获取缓存信息

        Returns:
            缓存统计信息
        """
        return {
            'cached_count': len(self._cache),
            'scripts_root': self.scripts_root,
            'scripts': list(self._cache.keys())
        }


# 全局单例
_global_loader: Optional[ScriptLoader] = None


def get_script_loader(scripts_root: str = None) -> ScriptLoader:
    """获取全局脚本加载器实例"""
    global _global_loader

    if _global_loader is None:
        _global_loader = ScriptLoader(scripts_root)

    return _global_loader
