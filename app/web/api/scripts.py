"""
脚本管理API
"""
import ast
import os
import sys
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.script_loader import get_script_loader, ScriptLoadError, ScriptValidationError
from app.core.database_models import Algorithm, ScriptVersion, Hook, AlgorithmHook, ScriptExecutionLog, db
from app import logger

# 创建蓝图
scripts_bp = Blueprint('scripts', __name__, url_prefix='/api/scripts')


def serialize_script_version(sv):
    """序列化脚本版本对象"""
    return {
        'id': sv.id,
        'algorithm_id': sv.algorithm_id,
        'version': sv.version,
        'script_path': sv.script_path,
        'file_hash': sv.file_hash,
        'content_hash': sv.content_hash,
        'changelog': sv.changelog,
        'is_active': sv.is_active,
        'created_at': sv.created_at.isoformat() if sv.created_at else None,
        'created_by': sv.created_by
    }


@scripts_bp.route('/', methods=['GET'])
def list_scripts():
    """
    列出所有脚本

    Query参数:
        - category: 脚本类别 ('detectors', 'filters', 'hooks', 'postprocessors')
        - include_stats: 是否包含统计信息 (true/false)
    """
    try:
        category = request.args.get('category')
        include_stats = request.args.get('include_stats', 'false').lower() == 'true'

        loader = get_script_loader()
        scripts = loader.list_scripts(category)

        # 如果需要统计信息，从数据库查询
        if include_stats:
            for script in scripts:
                try:
                    algo = Algorithm.get(Algorithm.script_path == script['path'])
                    script['algorithm_id'] = algo.id
                    script['algorithm_name'] = algo.name
                    script['status'] = 'active'

                    # 执行统计
                    logs = ScriptExecutionLog.select().where(
                        (ScriptExecutionLog.script_path == script['path']) &
                        (ScriptExecutionLog.executed_at >= datetime.now().replace(hour=0, minute=0, second=0, microsecond=0))
                    )

                    script['execution_stats'] = {
                        'total_calls': logs.count(),
                        'avg_time_ms': sum([log.execution_time_ms for log in logs]) / logs.count() if logs.count() > 0 else 0,
                        'success_rate': sum([1 for log in logs if log.success]) / logs.count() if logs.count() > 0 else 0,
                        'last_error': logs[-1].error_message if logs.count() > 0 and not logs[-1].success else None
                    }

                except Algorithm.DoesNotExist:
                    script['status'] = 'available'
                    script['execution_stats'] = None

        return jsonify({
            'success': True,
            'scripts': scripts
        })

    except Exception as e:
        logger.error(f"列出脚本失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@scripts_bp.route('/validate', methods=['POST'])
def validate_script():
    """
    验证脚本语法

    Request body:
        {
            "content": "脚本内容",
            "path": "脚本路径（可选）"
        }
    """
    try:
        data = request.get_json()
        content = data.get('content', '')
        path = data.get('path')

        if not content:
            return jsonify({
                'success': False,
                'error': '脚本内容不能为空'
            }), 400

        # 语法检查
        try:
            ast.parse(content)
            syntax_valid = True
            syntax_error = None
        except SyntaxError as e:
            syntax_valid = False
            syntax_error = {
                'line': e.lineno,
                'offset': e.offset,
                'message': e.msg
            }

        # 检查必需函数
        has_process = 'def process(' in content
        has_init = 'def init(' in content
        has_cleanup = 'def cleanup(' in content

        # 检查元数据
        has_metadata = 'SCRIPT_METADATA' in content

        return jsonify({
            'success': True,
            'validation': {
                'syntax_valid': syntax_valid,
                'syntax_error': syntax_error,
                'has_process': has_process,
                'has_init': has_init,
                'has_cleanup': has_cleanup,
                'has_metadata': has_metadata,
                'is_valid': syntax_valid and has_process
            }
        })

    except Exception as e:
        logger.error(f"验证脚本失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@scripts_bp.route('/upload', methods=['POST'])
def upload_script():
    """
    上传/创建脚本

    Request body:
        {
            "path": "detectors/my_detector.py",  # 相对于USER_SCRIPTS_ROOT的路径
            "content": "脚本内容",
            "category": "detectors",
            "create_algorithm": false  # 是否同时创建算法记录
        }
    """
    try:
        data = request.get_json()
        path = data.get('path')
        content = data.get('content', '')
        category = data.get('category', 'detectors')
        create_algorithm = data.get('create_algorithm', False)

        if not path:
            return jsonify({
                'success': False,
                'error': '必须指定脚本路径'
            }), 400

        # 获取脚本根目录
        loader = get_script_loader()
        abs_path = loader.resolve_path(path)

        # 创建目录
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)

        # 检查是否已存在
        if os.path.exists(abs_path):
            return jsonify({
                'success': False,
                'error': '脚本已存在'
            }), 400

        # 写入文件
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(content)

        # 验证语法
        try:
            loader.validate_syntax(abs_path)
        except ScriptValidationError as e:
            # 删除文件
            os.remove(abs_path)
            return jsonify({
                'success': False,
                'error': f'语法错误: {e}'
            }), 400

        # 如果需要，创建算法记录
        algorithm_id = None
        if create_algorithm:
            # 尝试从SCRIPT_METADATA提取信息
            metadata = {}
            try:
                exec(content, {'__name__': '__metadata__'}, {'SCRIPT_METADATA': metadata})
            except:
                pass

            algorithm = Algorithm.create(
                name=metadata.get('name', path[:-3]),
                plugin_module='script_algorithm',
                script_type='script',
                script_path=path,
                entry_function='process',
                script_version=metadata.get('version', 'v1.0'),
                runtime_timeout=metadata.get('timeout', 30),
                memory_limit_mb=metadata.get('memory_limit', 512),
                label_name=metadata.get('name', 'Custom'),
                interval_seconds=1
            )

            algorithm_id = algorithm.id

        return jsonify({
            'success': True,
            'path': path,
            'algorithm_id': algorithm_id,
            'message': '脚本创建成功'
        })

    except Exception as e:
        logger.error(f"上传脚本失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@scripts_bp.route('/<path:script_path>', methods=['GET'])
def get_script(script_path):
    """获取脚本内容和信息"""
    try:
        loader = get_script_loader()
        abs_path = loader.resolve_path(script_path)

        if not os.path.exists(abs_path):
            return jsonify({
                'success': False,
                'error': '脚本不存在'
            }), 404

        with open(abs_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 获取文件信息
        file_hash, content_hash = loader.calculate_hash(abs_path)
        mtime = os.path.getmtime(abs_path)

        # 查找关联的算法
        try:
            algo = Algorithm.get(Algorithm.script_path == script_path)
            algorithm_info = {
                'id': algo.id,
                'name': algo.name,
                'enabled': True  # 假设启用
            }
        except Algorithm.DoesNotExist:
            algorithm_info = None

        return jsonify({
            'success': True,
            'script': {
                'path': script_path,
                'content': content,
                'file_hash': file_hash,
                'content_hash': content_hash,
                'modified_time': mtime,
                'algorithm': algorithm_info
            }
        })

    except Exception as e:
        logger.error(f"获取脚本失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@scripts_bp.route('/<path:script_path>', methods=['PUT'])
def update_script(script_path):
    """
    更新脚本内容

    Request body:
        {
            "content": "新内容",
            "changelog": "更新说明",
            "create_version": true  # 是否创建版本记录
        }
    """
    try:
        data = request.get_json()
        content = data.get('content', '')
        changelog = data.get('changelog', '')
        create_version = data.get('create_version', True)

        loader = get_script_loader()
        abs_path = loader.resolve_path(script_path)

        if not os.path.exists(abs_path):
            return jsonify({
                'success': False,
                'error': '脚本不存在'
            }), 404

        # 验证语法
        try:
            loader.validate_syntax(abs_path)
        except ScriptValidationError as e:
            return jsonify({
                'success': False,
                'error': f'语法错误: {e}'
            }), 400

        # 如果需要创建版本，先保存旧版本
        if create_version:
            try:
                algo = Algorithm.get(Algorithm.script_path == script_path)

                # 读取当前内容
                with open(abs_path, 'r', encoding='utf-8') as f:
                    old_content = f.read()

                # 计算hash
                old_file_hash, old_content_hash = loader.calculate_hash(abs_path)

                # 获取当前版本号
                try:
                    latest_version = ScriptVersion.select().where(
                        ScriptVersion.algorithm == algo
                    ).order_by(ScriptVersion.created_at.desc()).first()

                    # 递增版本号
                    if latest_version:
                        version_parts = latest_version.version.replace('v', '').split('.')
                        version_parts[-1] = str(int(version_parts[-1]) + 1)
                        new_version = 'v' + '.'.join(version_parts)
                    else:
                        new_version = 'v1.1'

                except Exception:
                    new_version = 'v1.1'

                # 创建版本记录
                ScriptVersion.create(
                    algorithm=algo,
                    version=new_version,
                    script_path=script_path,
                    file_hash=old_file_hash,
                    content_hash=old_content_hash,
                    changelog=changelog or f"Backup before update",
                    is_active=False,
                    created_at=datetime.now(),
                    created_by='api'
                )

            except Algorithm.DoesNotExist:
                pass  # 没有关联算法，不创建版本

        # 写入新内容
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(content)

        return jsonify({
            'success': True,
            'message': '脚本更新成功'
        })

    except Exception as e:
        logger.error(f"更新脚本失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@scripts_bp.route('/<path:script_path>', methods=['DELETE'])
def delete_script(script_path):
    """删除脚本（软删除，移到回收站）"""
    try:
        loader = get_script_loader()
        abs_path = loader.resolve_path(script_path)

        if not os.path.exists(abs_path):
            return jsonify({
                'success': False,
                'error': '脚本不存在'
            }), 404

        # 创建回收站目录
        trash_dir = os.path.join(loader.scripts_root, '.trash')
        os.makedirs(trash_dir, exist_ok=True)

        # 移动文件
        trash_path = os.path.join(trash_dir, f"{script_path.replace('/', '_')}_{datetime.now().timestamp()}")
        os.rename(abs_path, trash_path)

        return jsonify({
            'success': True,
            'message': '脚本已删除'
        })

    except Exception as e:
        logger.error(f"删除脚本失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@scripts_bp.route('/algorithms/<int:algorithm_id>/versions', methods=['GET'])
def get_script_versions(algorithm_id):
    """获取脚本版本历史"""
    try:
        versions = ScriptVersion.select().where(
            ScriptVersion.algorithm == algorithm_id
        ).order_by(ScriptVersion.created_at.desc())

        return jsonify({
            'success': True,
            'versions': [serialize_script_version(v) for v in versions]
        })

    except Exception as e:
        logger.error(f"获取版本历史失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@scripts_bp.route('/algorithms/<int:algorithm_id>/rollback', methods=['POST'])
def rollback_script(algorithm_id):
    """
    回滚脚本到指定版本

    Request body:
        {
            "version": "v1.0"
        }
    """
    try:
        data = request.get_json()
        version = data.get('version')

        if not version:
            return jsonify({
                'success': False,
                'error': '必须指定版本号'
            }), 400

        # 查找版本记录
        try:
            script_version = ScriptVersion.get(
                (ScriptVersion.algorithm == algorithm_id) &
                (ScriptVersion.version == version)
            )
        except ScriptVersion.DoesNotExist:
            return jsonify({
                'success': False,
                'error': '版本不存在'
            }), 404

        # 获取文件路径（从版本记录或当前算法）
        loader = get_script_loader()
        abs_path = loader.resolve_path(script_version.script_path)

        # 注意：这里只保存了hash，没有保存完整内容
        # 生产环境应该将版本内容保存在数据库或对象存储中
        return jsonify({
            'success': False,
            'error': '版本内容恢复功能需要配置版本存储'
        }), 400

    except Exception as e:
        logger.error(f"回滚脚本失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@scripts_bp.route('/templates', methods=['GET'])
def get_templates():
    """获取脚本模板列表"""
    try:
        templates_dir = os.path.join(get_script_loader().scripts_root, 'templates')
        templates = []

        if os.path.exists(templates_dir):
            for file in os.listdir(templates_dir):
                if file.endswith('.py') and not file.startswith('_'):
                    file_path = os.path.join(templates_dir, file)
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()

                    templates.append({
                        'name': file[:-3],
                        'path': f'templates/{file}',
                        'content': content
                    })

        return jsonify({
            'success': True,
            'templates': templates
        })

    except Exception as e:
        logger.error(f"获取模板失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


def register_scripts_api(app):
    """注册脚本API到Flask应用"""
    app.register_blueprint(scripts_bp)
