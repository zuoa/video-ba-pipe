# Video BA Pipe

视频流智能分析系统，支持多路视频源接入、工作流编排、算法脚本管理、告警落库与消息发布。

## 核心能力

- 多视频源：支持 RTSP/HTTP/本地文件
- 可视化工作流：基于节点连接定义分析流程
- 脚本算法：支持 Python 脚本上传、编辑与测试
- 告警闭环：保存告警图片/视频并提供检索
- 消息集成：可通过 RabbitMQ 发布预警事件

## 系统组成

- `api`：Flask + Gunicorn Web API（默认 `5002`）
- `worker`：视频解码与工作流执行进程
- `frontend`：前端管理界面（默认 `8080`）
- `rabbitmq`：可选消息队列（在 CUDA compose 中内置）

## 快速开始（Docker 推荐）

### 1) CPU 部署

```bash
docker compose -f docker-compose.yml up -d
```

说明：`docker-compose.yml` 默认将 `RABBITMQ_HOST` 设为 `rabbitmq`，但未内置 RabbitMQ 服务。  
如不使用消息队列，请先把 `api/worker` 的 `RABBITMQ_ENABLED` 改为 `false`，或接入外部 RabbitMQ。

### 2) CUDA/GPU 部署

```bash
docker compose -f docker-compose.yml.cuda up -d
```

要求：宿主机已安装 NVIDIA 驱动与 Docker NVIDIA Runtime。

### 3) RK3588/NPU 部署

```bash
docker compose -f docker-compose.yml.rknn up -d
```

说明：RK 部署默认改为 `WEB_CONCURRENCY=1`，并启用 SQLite 的 `WAL + busy_timeout`，避免 `api + worker + workflow_worker` 多进程同时访问同一数据库文件时频繁出现 `database is locked`。

## 访问地址

- 前端：`http://localhost:8080`
- 后端 API：`http://localhost:5002`
- RabbitMQ 管理台（CUDA compose）：`http://localhost:15672`（`admin/admin123`）

## 本地开发（非 Docker）

### 后端

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 初始化数据库
python app/setup_database.py

# 终端 1：启动 worker
python app/main.py

# 终端 2：启动 API
python app/web/webapp.py
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

开发环境访问：`http://localhost:8000`

## 环境变量

复制模板并按需调整：

```bash
cp env.example .env
```

重点配置项：

- `DB_PATH`：数据库路径
- `FRAME_SAVE_PATH` / `VIDEO_SAVE_PATH` / `VIDEO_SOURCE_PATH`：媒体存储目录
- `RECORDING_ENABLED`：是否录制预警视频
- `ANALYSIS_TARGET_FPS` / `ANALYSIS_BUFFER_SECONDS`：分析链路缓冲参数
- `PRE_ALERT_DURATION` / `POST_ALERT_DURATION` / `RECORDING_BUFFER_DURATION`：录制链路缓冲参数
- `RECORDING_JPEG_QUALITY` / `RECORDING_COMPRESSED_MAX_BYTES`：录制压缩帧缓存参数
- `IS_EXTREME_DECODE_MODE`：极速解码（仅保留最新帧）
- `RABBITMQ_ENABLED`：是否启用 RabbitMQ 发布

## 目录结构

```text
.
├── app/                  # 后端服务与工作流引擎
├── frontend/             # 前端管理界面（UmiJS + React）
├── docs/                 # 部署和集成文档
├── docker-compose.yml
├── docker-compose.yml.cuda
├── docker-compose.yml.rknn
└── env.example
```

## 相关文档

- RK3588 镜像与构建说明：`docs/rk3588_docker.md`
- RK3588 板端部署/排障：`docs/rk_usage_manual.md`
- RabbitMQ 消息格式与接入：`docs/rabbitmq_integration.md`
- 前端说明：`frontend/README.md`
