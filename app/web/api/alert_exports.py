"""Alert export APIs."""

from flask import Response, jsonify, request, send_file

from app.core.alert_export import (
    ExportValidationError,
    cancel_export_task,
    create_export_task,
    delete_export_task,
    resolve_export_file,
    serialize_export_task,
    x_accel_redirect_path,
)
from app.core.database_models import AlertExportTask
from app.web.api.auth import (
    apply_owner_scope,
    current_username,
    is_admin_user,
    require_auth,
    require_resource_owner,
)


def _nginx_x_accel_enabled() -> bool:
    flag = request.headers.get('X-Accel-Redirect-Enabled', '')
    return flag.strip().lower() in {'1', 'true', 'yes', 'on'}


def _request_filters():
    if request.method == 'GET':
        return request.args
    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        return payload
    return request.args


def _load_task(task_id: int):
    try:
        task = AlertExportTask.get_by_id(task_id)
    except AlertExportTask.DoesNotExist:
        return None, (jsonify({'error': '导出任务不存在'}), 404)
    owner_response = require_resource_owner(task)
    if owner_response:
        return None, owner_response
    return task, None


def register_alert_exports_api(app):
    @app.route('/media/exports/<path:file_path>', methods=['GET'])
    def download_public_export(file_path):
        resolved = resolve_export_file(file_path)
        if resolved is None or not resolved.is_file():
            return jsonify({'error': '导出文件不存在或已被清理'}), 404
        if resolved.suffix.lower() != '.zip':
            return jsonify({'error': '不支持的导出文件类型'}), 400
        return send_file(
            resolved,
            as_attachment=True,
            download_name=resolved.name,
            mimetype='application/zip',
            conditional=True,
        )

    @app.route('/api/alert-exports', methods=['POST'])
    @require_auth
    def create_alert_export():
        try:
            task = create_export_task(
                _request_filters(),
                username=current_username('admin'),
                is_admin=is_admin_user(),
            )
        except ExportValidationError as exc:
            return jsonify({'error': str(exc)}), exc.status_code
        return jsonify({
            **serialize_export_task(task),
            'message': '导出进行中',
        }), 201

    @app.route('/api/alert-exports', methods=['GET'])
    @require_auth
    def list_alert_exports():
        page = max(1, int(request.args.get('page', 1)))
        per_page = min(100, max(1, int(request.args.get('per_page', 20))))
        query = apply_owner_scope(
            AlertExportTask.select(),
            AlertExportTask,
        ).order_by(AlertExportTask.id.desc())
        total = query.count()
        total_pages = (total + per_page - 1) // per_page if total else 0
        offset = (page - 1) * per_page
        tasks = list(query.limit(per_page).offset(offset))
        return jsonify({
            'data': [serialize_export_task(task) for task in tasks],
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'total_pages': total_pages,
            },
        })

    @app.route('/api/alert-exports/<int:task_id>', methods=['GET'])
    @require_auth
    def get_alert_export(task_id):
        task, error = _load_task(task_id)
        if error:
            return error
        return jsonify(serialize_export_task(task))

    @app.route('/api/alert-exports/<int:task_id>/download', methods=['GET'])
    @require_auth
    def download_alert_export(task_id):
        task, error = _load_task(task_id)
        if error:
            return error
        if task.status != 'succeeded' or not task.file_path:
            return jsonify({'error': '导出尚未完成，无法下载'}), 409
        resolved = resolve_export_file(task.file_path)
        if resolved is None or not resolved.is_file():
            return jsonify({'error': '导出文件不存在或已被清理'}), 404
        download_name = task.file_name or resolved.name
        accel_path = x_accel_redirect_path(task.file_path)
        if accel_path and _nginx_x_accel_enabled():
            response = Response(status=200)
            response.headers['X-Accel-Redirect'] = accel_path
            response.headers['Content-Type'] = 'application/zip'
            response.headers['Content-Disposition'] = f'attachment; filename="{download_name}"'
            return response
        return send_file(
            resolved,
            as_attachment=True,
            download_name=download_name,
            mimetype='application/zip',
        )

    @app.route('/api/alert-exports/<int:task_id>/cancel', methods=['POST'])
    @require_auth
    def cancel_alert_export(task_id):
        task, error = _load_task(task_id)
        if error:
            return error
        try:
            task = cancel_export_task(task)
        except ExportValidationError as exc:
            return jsonify({'error': str(exc)}), exc.status_code
        return jsonify(serialize_export_task(task))

    @app.route('/api/alert-exports/<int:task_id>', methods=['DELETE'])
    @require_auth
    def delete_alert_export(task_id):
        task, error = _load_task(task_id)
        if error:
            return error
        try:
            delete_export_task(task)
        except ExportValidationError as exc:
            return jsonify({'error': str(exc)}), exc.status_code
        return jsonify({'success': True})
