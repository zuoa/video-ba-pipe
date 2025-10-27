import importlib
import inspect
import os
import sys

from app.core.algorithm import BaseAlgorithm


class PluginManager:
    def __init__(self, plugin_folder="plugins"):
        self.plugin_folder = plugin_folder
        self.algorithms = {}  # 按算法名称索引
        self.algorithms_by_module = {}  # 按模块名索引
        self.last_mtime = {}  # 记录文件最后修改时间，用于热重载
        self.discover_plugins()

    def discover_plugins(self):
        """发现并加载指定文件夹下的所有插件"""
        print(f"[PluginManager] 正在扫描插件目录: {self.plugin_folder}")

        if not os.path.exists(self.plugin_folder):
            print(f"[PluginManager] 插件目录不存在: {self.plugin_folder}")
            return

        for filename in os.listdir(self.plugin_folder):
            if filename.endswith(".py") and not filename.startswith("__"):
                # 规范化路径
                normalized_path = os.path.normpath(self.plugin_folder)
                
                # 如果是绝对路径，需要找到项目根目录并转换为相对路径
                if os.path.isabs(normalized_path):
                    # 找到项目根目录（包含app目录的目录）
                    # 通过查找'app'目录在路径中的位置来确定
                    path_parts = normalized_path.split(os.sep)
                    try:
                        # 找到最后一个'app'的索引（处理 /app/app/plugins 这种情况）
                        app_indices = [i for i, part in enumerate(path_parts) if part == 'app']
                        if app_indices:
                            # 使用最后一个'app'作为起点
                            app_index = app_indices[-1]
                            # 从app开始重建相对路径
                            relative_parts = path_parts[app_index:]
                            module_name = '.'.join(relative_parts) + '.' + filename[:-3]
                        else:
                            # 如果路径中没有'app'，则使用最后两个目录
                            module_name = '.'.join(path_parts[-2:]) + '.' + filename[:-3]
                    except (ValueError, IndexError):
                        # 如果路径中没有'app'，则使用最后两个目录
                        module_name = '.'.join(path_parts[-2:]) + '.' + filename[:-3]
                else:
                    # 相对路径，直接转换
                    module_name = normalized_path.replace('/', '.').replace('\\', '.') + '.' + filename[:-3]
                
                module_file_name = filename[:-3]  # 去掉.py扩展名
                self._load_module(module_name, module_file_name)

    def _load_module(self, module_name, module_file_name, reload_module=False):
        try:
            # 构建正确的模块文件路径
            module_path = os.path.join(self.plugin_folder, module_file_name + '.py')
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
                    algo_instance_for_name = obj(algo_config={'models': []})  # 临时实例以获取名称
                    algo_name = algo_instance_for_name.name
                    self.algorithms[algo_name] = obj  # 按算法名称索引
                    self.algorithms_by_module[module_file_name] = obj  # 按模块文件名索引
                    self.last_mtime[module_name] = current_mtime
                    print(f"  -> 发现算法插件: '{algo_name}' (模块: {module_file_name})")

        except Exception as e:
            print(f"[PluginManager] 加载插件模块 '{module_name}' 失败: {e}")

    def get_algorithm_class(self, name: str):
        """根据名称获取算法类"""
        return self.algorithms.get(name)
    
    def get_algorithm_class_by_module(self, module_name: str):
        """根据模块名获取算法类"""
        return self.algorithms_by_module.get(module_name)

    def check_for_updates(self):
        """检查插件文件夹是否有更新（新增或修改的文件）"""
        # 简单实现：重新扫描
        # 更高效的实现可以使用 inotify/watchdog 库来监控文件系统事件
        self.discover_plugins()
