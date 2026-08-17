"""License status and installation API."""

from flask import jsonify, request

from app.core.license_service import (
    LicenseError,
    MAX_LICENSE_BYTES,
    install_license,
    serialize_status,
)
from app.web.api.auth import current_username, is_admin_user, require_admin, require_auth


def register_license_api(app):
    @app.route('/api/license/status', methods=['GET'])
    @require_auth
    def get_license_status():
        return jsonify({
            'success': True,
            **serialize_status(include_details=is_admin_user()),
        })

    @app.route('/api/license/install', methods=['POST'])
    @require_auth
    @require_admin
    def install_license_file():
        uploaded = request.files.get('file')
        if uploaded is not None:
            raw = uploaded.stream.read(MAX_LICENSE_BYTES + 1)
            if len(raw) > MAX_LICENSE_BYTES:
                return jsonify({
                    'success': False,
                    'code': 'license_file_invalid',
                    'error': '许可证文件超过 64 KiB',
                }), 400
            try:
                token = raw.decode('utf-8')
            except UnicodeDecodeError:
                return jsonify({
                    'success': False,
                    'code': 'license_file_invalid',
                    'error': '许可证文件必须是 UTF-8 文本',
                }), 400
        else:
            token = str((request.get_json(silent=True) or {}).get('token') or '')

        try:
            install_license(token, installed_by=current_username('admin'))
        except LicenseError as exc:
            return jsonify({'success': False, **exc.to_dict()}), 400
        return jsonify({
            'success': True,
            'message': '许可证安装成功',
            **serialize_status(include_details=True),
        })
