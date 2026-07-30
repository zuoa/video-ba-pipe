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
当前 compose 已内置 PostgreSQL，应用默认通过 `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD` 连接数据库。  
如需改用外部 PostgreSQL，可在 `.env` 中覆盖 `DB_HOST`、`DB_PORT`、`DB_NAME`、`DB_USER`、`DB_PASSWORD`。

### 2) CUDA/GPU 部署

```bash
docker compose -f docker-compose.yml.cuda up -d
```

要求：宿主机已安装 NVIDIA 驱动与 Docker NVIDIA Runtime。

### 3) RK3588/NPU 部署

```bash
docker compose -f docker-compose.yml.rknn up -d
```

说明：RK 部署默认内置 PostgreSQL，并保持 `WEB_CONCURRENCY=1` 作为稳妥默认值，避免盒子上额外放大 API 侧并发压力。
如需启用 RK 硬解，推荐先构建独立的 `ffmpeg+rkmpp` 基础镜像，再由 `Dockerfile.rk` 通过 `COPY --from` 复用；在 CI 中会自动按当前仓库 owner 选择对应的基础镜像，部署时 compose 仍可保持拉取远程镜像。

### 4) Jetson Orin NX Super 16GB 部署

```bash
docker compose -f docker-compose.yml.jetson up -d
```

要求：目标设备运行 JetPack 6.2.1 / L4T 36.4.4，并已安装 Docker NVIDIA Runtime。后端使用独立的 `:jetson` ARM64 镜像，CUDA 推理由 NVIDIA PyTorch iGPU 运行时提供，H.264/H.265 默认使用 `nvv4l2decoder` 硬解；启动视频源时会用 `ffprobe` 探测并保存实际编码。前端复用通用 `:arm64` 镜像。Super 功耗模式需在宿主机刷机和 `nvpmodel` 配置中启用。

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

# 本地直跑默认使用 SQLite 初始化数据库
python app/setup_database.py

# 终端 1：启动 worker
python app/main.py

# 终端 2：启动 API
python app/web/webapp.py
```

如需本地直跑也接 PostgreSQL，请先显式设置 `DB_BACKEND=postgres` 和 `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD`，并确保数据库已预先创建。

如需将旧 SQLite 数据库迁移到 PostgreSQL：

```bash
python scripts/migrate_sqlite_to_postgres.py --sqlite-path ./app/data/db/ba.db
```

如果目标 PostgreSQL 是 compose 内置容器，推荐在容器里执行迁移脚本：

```bash
docker compose run --rm -v ./data:/data api python /app/scripts/migrate_sqlite_to_postgres.py --sqlite-path /data/db/ba.db
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

- `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD`：PostgreSQL 连接配置
- `DB_SSLMODE`：PostgreSQL SSL 模式
- `FRAME_SAVE_PATH` / `VIDEO_SAVE_PATH` / `VIDEO_SOURCE_PATH`：媒体存储目录
- `HF_USE_MIRROR` / `HF_MIRROR_ENDPOINT`：Hugging Face 模型拉取的默认镜像开关和镜像地址；模型导入弹窗也可逐次选择官方源或国内镜像
- `HF_DOWNLOAD_TIMEOUT_SECONDS` / `WEB_REQUEST_TIMEOUT_SECONDS` / `HF_TOKEN`：Hugging Face 下载超时、Web 请求超时及私有仓库访问 Token
- `RECORDING_ENABLED`：是否录制预警视频
- `VIDEO_DECODER_TYPE`：默认视频解码器类型；RK3588 推荐 `rk_mpp`，Jetson 推荐 `jetson_gst`
- `ANALYSIS_TARGET_FPS` / `ANALYSIS_BUFFER_SECONDS`：分析链路缓冲参数
- `PRE_ALERT_DURATION` / `POST_ALERT_DURATION` / `RECORDING_BUFFER_DURATION`：录制链路缓冲参数
- `RECORDING_JPEG_QUALITY` / `RECORDING_COMPRESSED_MAX_BYTES`：录制压缩帧缓存参数
- `IS_EXTREME_DECODE_MODE`：极速解码（仅保留最新帧）
- `RESOURCE_PROFILING_ENABLED`：输出帧拷贝、录制编码、工作流执行等性能埋点
- `WORKFLOW_ZERO_COPY_FRAMES`：source host 使用共享内存只读视图读取最新帧，减少复制（需确保处理耗时小于缓冲窗口）
- `SOURCE_HOST_WORKFLOW_NODE_WORKERS`：实时工作流同层节点并行 worker 数，`0` 表示关闭
- `RABBITMQ_ENABLED`：是否启用 RabbitMQ 发布

## 资源估算

在没有目标硬件时，可先用静态估算脚本评估多路视频的 buffer 内存压力：

```bash
python scripts/estimate_video_resources.py --source 1920x1080:25 --count 16
```

默认会读取当前环境变量；也可以不连数据库，直接传 `--source` / `--count` 估算 analysis 共享内存、recording 共享内存和 decoder 队列占用。

## 目录结构

```text
.
├── app/                  # 后端服务与工作流引擎
├── frontend/             # 前端管理界面（UmiJS + React）
├── docs/                 # 部署和集成文档
├── docker-compose.yml
├── docker-compose.yml.cuda
├── docker-compose.yml.jetson
├── docker-compose.yml.rknn
└── env.example
```

## 相关文档

- Docker 镜像构建说明：`docs/docker_build_workflows.md`
- Jetson Orin NX Super 镜像与部署：`docs/jetson_orin_nx_docker.md`
- RK3588 镜像与构建说明：`docs/rk3588_docker.md`
- RK3588 板端部署/排障：`docs/rk_usage_manual.md`
- RabbitMQ 消息格式与接入：`docs/rabbitmq_integration.md`
- 前端说明：`frontend/README.md`
