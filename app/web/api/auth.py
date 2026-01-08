import os
import jwt
import hashlib
from datetime import datetime, timedelta
from functools import wraps
from flask import Blueprint, request, jsonify, current_app

from app.core.database_models import User

auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')

SECRET_KEY = os.environ.get('JWT_SECRET', 'your-secret-key-change-in-production')
TOKEN_EXPIRE_HOURS = 24

def generate_token(user_id, username, role):
    payload = {
        'user_id': user_id,
        'username': username,
        'role': role,
        'exp': datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm='HS256')

def verify_token(token):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Unauthorized'}), 401
        
        token = auth_header.split(' ')[1]
        payload = verify_token(token)
        if not payload:
            return jsonify({'error': 'Invalid token'}), 401
        
        request.user = payload
        return f(*args, **kwargs)
    return decorated

def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.user.get('role') != 'admin':
            return jsonify({'error': 'Forbidden'}), 403
        return f(*args, **kwargs)
    return decorated

@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'success': False, 'error': '用户名和密码不能为空'}), 400
    
    user = User.select().where(User.username == username).first()
    
    if not user:
        return jsonify({'success': False, 'error': '用户名或密码错误'}), 401
    
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    if user.password_hash != password_hash:
        return jsonify({'success': False, 'error': '用户名或密码错误'}), 401
    
    if not user.enabled:
        return jsonify({'success': False, 'error': '用户已被禁用'}), 403
    
    user.last_login = datetime.now()
    user.save()
    
    token = generate_token(user.id, user.username, user.role)
    
    return jsonify({
        'success': True,
        'token': token,
        'user': {
            'id': user.id,
            'username': user.username,
            'role': user.role
        }
    })

@auth_bp.route('/current', methods=['GET'])
@require_auth
def get_current_user():
    user_id = request.user['user_id']
    user = User.get_by_id(user_id)
    
    return jsonify({
        'success': True,
        'user': {
            'id': user.id,
            'username': user.username,
            'role': user.role,
            'last_login': user.last_login.isoformat() if user.last_login else None
        }
    })

@auth_bp.route('/users', methods=['GET'])
@require_auth
@require_admin
def list_users():
    users = User.select()
    return jsonify({
        'success': True,
        'users': [{
            'id': u.id,
            'username': u.username,
            'role': u.role,
            'enabled': u.enabled,
            'created_at': u.created_at.isoformat(),
            'last_login': u.last_login.isoformat() if u.last_login else None
        } for u in users]
    })

@auth_bp.route('/users', methods=['POST'])
@require_auth
@require_admin
def create_user():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    role = data.get('role', 'user')
    
    if not username or not password:
        return jsonify({'success': False, 'error': '用户名和密码不能为空'}), 400
    
    if User.select().where(User.username == username).exists():
        return jsonify({'success': False, 'error': '用户名已存在'}), 400
    
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    
    user = User.create(
        username=username,
        password_hash=password_hash,
        role=role,
        created_at=datetime.now()
    )
    
    return jsonify({
        'success': True,
        'user': {
            'id': user.id,
            'username': user.username,
            'role': user.role
        }
    })

@auth_bp.route('/users/<int:user_id>', methods=['PUT'])
@require_auth
@require_admin
def update_user(user_id):
    data = request.json
    user = User.get_by_id(user_id)
    
    if 'password' in data and data['password']:
        user.password_hash = hashlib.sha256(data['password'].encode()).hexdigest()
    
    if 'role' in data:
        user.role = data['role']
    
    if 'enabled' in data:
        user.enabled = data['enabled']
    
    user.save()
    
    return jsonify({'success': True})

@auth_bp.route('/users/<int:user_id>', methods=['DELETE'])
@require_auth
@require_admin
def delete_user(user_id):
    if user_id == request.user['user_id']:
        return jsonify({'success': False, 'error': '不能删除自己'}), 400
    
    user = User.get_by_id(user_id)
    user.delete_instance()
    
    return jsonify({'success': True})

