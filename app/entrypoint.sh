#!/bin/sh

# 退出脚本，如果任何命令失败
set -e

# 等待数据库服务可用 (可选但推荐，特别是如果数据库在另一个容器中启动)
# 这需要安装 netcat (nc) 或者其他工具
# while ! nc -z $DB_HOST $DB_PORT; do
#   echo "Waiting for database..."
#   sleep 1
# done
# echo "Database is up!"

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