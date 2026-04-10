#!/bin/sh

# 退出脚本，如果任何命令失败
set -e

DB_HOST="${DB_HOST:-postgres}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-video_ba_pipe}"
DB_NAME="${DB_NAME:-video_ba_pipe}"

echo "Waiting for PostgreSQL at ${DB_HOST}:${DB_PORT}..."
until pg_isready -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" >/dev/null 2>&1; do
  sleep 1
done
echo "PostgreSQL is up."

echo "Initializing database..."
# 确保 FLASK_APP 环境变量已设置，或者在 flask 命令中指定 --app
# 例如: export FLASK_APP=your_application_module:create_app()
# 或者: flask --app your_application_module:create_app() init-db
#flask init-db


echo "Starting scheduler..."
python main.py &


echo "Starting Gunicorn..."
# 用 exec "$@" 来执行 CMD 中指定的命令，或者直接启动 Gunicorn
# exec "$@"
gunicorn  -w 4 --bind 0.0.0.0:5000 web.webapp:app # 根据您的应用调整
