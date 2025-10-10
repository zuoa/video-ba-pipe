import importlib
import inspect
import os

from app.core.algorithm import BaseAlgorithm


class PluginManager:
    def __init__(self, plugin_folder="plugins"):
        self.plugin_folder = plugin_folder
        self.algorithms = {}
        self.last_mtime = {}  # 记录文件最后修改时间，用于热重载
        self.discover_plugins()

    def discover_plugins(self):
        """发现并加载指定文件夹下的所有插件"""
        print("[PluginManager] 正在扫描插件...")

        for filename in os.listdir(self.plugin_folder):
            if filename.endswith(".py") and not filename.startswith("__"):
                module_name = f"{self.plugin_folder}.{filename[:-3]}"
                self._load_module(module_name)

    def _load_module(self, module_name, reload_module=False):
        try:
            module_path = module_name.replace('.', '/') + '.py'
            current_mtime = os.path.getmtime(module_path)

            if module_name in self.last_mtime and self.last_mtime[module_name] == current_mtime and not reload_module:
                return  # 文件未改变，无需加载

            if reload_module and module_name in self.algorithms.values():
                module = importlib.reload(sys.modules[module_name])
                print(f"[PluginManager] 模块 '{module_name}' 已热重载。")
            else:
                module = importlib.import_module(module_name)
                print(f"[PluginManager] 模块 '{module_name}' 已加载。")

            # 遍历模块成员，寻找继承自 BaseAlgorithm 的类
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, BaseAlgorithm) and obj is not BaseAlgorithm:
                    algo_instance_for_name = obj(algo_config={'model_path': ''})  # 临时实例以获取名称
                    algo_name = algo_instance_for_name.name
                    self.algorithms[algo_name] = obj  # 存储的是类本身，而不是实例
                    self.last_mtime[module_name] = current_mtime
                    print(f"  -> 发现算法插件: '{algo_name}'")

        except Exception as e:
            print(f"[PluginManager] 加载插件模块 '{module_name}' 失败: {e}")

    def get_algorithm_class(self, name: str):
        """根据名称获取算法类"""
        return self.algorithms.get(name)

    def check_for_updates(self):
        """检查插件文件夹是否有更新（新增或修改的文件）"""
        # 简单实现：重新扫描
        # 更高效的实现可以使用 inotify/watchdog 库来监控文件系统事件
        self.discover_plugins()
