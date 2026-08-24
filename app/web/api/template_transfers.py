"""Admin APIs for portable workflow-template transfer packages."""

from __future__ import annotations

import json
import os
import tempfile

from flask import after_this_request, jsonify, request, send_file

from app.config import TEMPLATE_TRANSFER_PATH
from app.core.database_models import Workflow
from app.core.license_service import LicenseError
from app.core.template_transfer import (
    MAX_PACKAGE_BYTES,
    TemplateTransferError,
    build_export_package,
    import_package,
    preflight_manifest,
    transfer_profile,
)
from app.web.api.auth import current_username, require_admin, require_auth


def register_template_transfer_api(app):
    @app.route('/api/workflow-template-transfers/capabilities', methods=['GET'])
    @require_auth
    @require_admin
    def template_transfer_capabilities():
        profile = transfer_profile()
        return jsonify({
            'success': True,
            **profile,
            'configured': bool(profile.get('device_model_code')),
            'schema_versions': [1],
        })

    @app.route('/api/workflow-templates/<int:template_id>/export', methods=['POST'])
    @require_auth
    @require_admin
    def export_workflow_template(template_id):
        package_path = None
        try:
            template = Workflow.get_by_id(template_id)
            data = request.get_json(silent=True) or {}
            include_models = data.get('include_models', False)
            if not isinstance(include_models, bool):
                return jsonify({'code': 'invalid_include_models', 'error': 'include_models 必须是布尔值'}), 400
            package_path, filename = build_export_package(
                template,
                include_models=include_models,
            )

            @after_this_request
            def cleanup(response):
                try:
                    if package_path and os.path.exists(package_path):
                        os.remove(package_path)
                except OSError:
                    app.logger.warning('清理模板导出临时文件失败: %s', package_path, exc_info=True)
                return response

            return send_file(
                package_path,
                as_attachment=True,
                download_name=filename,
                mimetype='application/zip',
            )
        except Workflow.DoesNotExist:
            return jsonify({'code': 'template_not_found', 'error': '编排模板不存在'}), 404
        except TemplateTransferError as exc:
            return jsonify(exc.to_dict()), 400
        except Exception as exc:
            if package_path and os.path.exists(package_path):
                os.remove(package_path)
            app.logger.exception('导出编排模板失败: %s', exc)
            return jsonify({'error': str(exc)}), 500

    @app.route('/api/workflow-template-imports/preflight', methods=['POST'])
    @require_auth
    @require_admin
    def preflight_workflow_template_import():
        try:
            data = request.get_json(silent=True) or {}
            manifest = data.get('manifest', data)
            resolutions = data.get('resolutions') if isinstance(data, dict) else None
            return jsonify({'success': True, **preflight_manifest(manifest, resolutions)})
        except TemplateTransferError as exc:
            status = 409 if exc.code == 'device_model_mismatch' else 400
            return jsonify(exc.to_dict()), status
        except Exception as exc:
            app.logger.exception('编排模板导入预检失败: %s', exc)
            return jsonify({'error': str(exc)}), 500

    @app.route('/api/workflow-template-imports', methods=['POST'])
    @require_auth
    @require_admin
    def import_workflow_template():
        # 模型迁移包可大于全局 1 GiB 上传上限；该路由仍受迁移包自身
        # 9 GiB 请求上限和解压后的 8 GiB 内容上限保护。
        request.max_content_length = MAX_PACKAGE_BYTES
        upload = request.files.get('file')
        if upload is None or not upload.filename:
            return jsonify({'code': 'package_required', 'error': '请选择迁移包'}), 400
        try:
            resolutions = json.loads(request.form.get('resolutions') or '{}')
        except json.JSONDecodeError:
            return jsonify({'code': 'invalid_resolutions', 'error': '导入处理方案 JSON 无效'}), 400
        if not isinstance(resolutions, dict):
            return jsonify({'code': 'invalid_resolutions', 'error': '导入处理方案必须是对象'}), 400

        os.makedirs(TEMPLATE_TRANSFER_PATH, exist_ok=True)
        handle, package_path = tempfile.mkstemp(
            prefix='workflow-template-import-', suffix='.zip', dir=TEMPLATE_TRANSFER_PATH
        )
        os.close(handle)
        try:
            upload.save(package_path)
            result = import_package(
                package_path,
                resolutions,
                username=current_username('admin'),
            )
            return jsonify(result), 201 if not result.get('already_imported') else 200
        except TemplateTransferError as exc:
            status = 409 if exc.code in {
                'device_model_mismatch',
                'import_requirements_unresolved',
                'model_name_conflict',
            } else 400
            return jsonify(exc.to_dict()), status
        except LicenseError as exc:
            return jsonify(exc.to_dict()), 403
        except Exception as exc:
            app.logger.exception('导入编排模板失败: %s', exc)
            return jsonify({'error': str(exc)}), 500
        finally:
            try:
                if os.path.exists(package_path):
                    os.remove(package_path)
            except OSError:
                app.logger.warning('清理模板导入临时文件失败: %s', package_path, exc_info=True)
