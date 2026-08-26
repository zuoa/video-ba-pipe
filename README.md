# Video BA Pipe

离线许可证、节点绑定和永久免费单路试用规则见 [docs/license.md](docs/license.md)。跨平台千人级人脸识别的模型契约、录入格式和验收方法见 [docs/face_recognition.md](docs/face_recognition.md)。

视频流智能分析系统，支持多路视频源接入、工作流编排、算法脚本管理、告警落库与消息发布。

## 核心能力

- 多视频源：支持 RTSP/HTTP/本地文件
- 可视化工作流：基于节点连接定义分析流程
- 脚本算法：支持 Python 脚本上传、编辑与测试
- 组合检测：用画布连接 YOLO/ONNX/RKNN 检测数据流与 AND、OR、NOT 业务规则
- 千人级人脸识别：同一业务契约自动适配 x86 CPU/CUDA、Jetson 与 RK3588
- 告警闭环：保存告警图片/视频并提供检索
- 消息集成：默认通过 MQTT 发布预警事件，也可切换到 RabbitMQ 或 HTTP API

## 系统组成

- `db-init`：一次性数据库初始化与迁移（轻量 control 镜像）
- `api`：Flask + Gunicorn Web API（默认 `5002`）
- `jobs`：告警导出、告警投递、人脸批量录入和媒体清理（轻量 control 镜像）
- `worker`：视频解码、算法编排与推理（对应平台的重型 worker 镜像）
- `frontend`：前端管理界面（默认 `8080`）
- `mqtt`：可选的内置 Mosquitto Broker
- `rabbitmq`：可选的内置 RabbitMQ
- `http`：直接异步投递到外部接收端 API

## 快速开始（Docker 推荐）

### 交互式生成 Compose（推荐）

仓库提供了 Compose 生成脚本，可自动识别 x86 CPU、x86 CUDA、Jetson 和 RK3588，按需加入 MQTT、RabbitMQ、MediaMTX，并可选择南京大学 GHCR 镜像：

```bash
./scripts/generate_compose.sh
docker compose up -d
```

无需克隆仓库也可以在目标部署目录直接远程运行。下面的写法会保留终端标准输入，因此可以正常完成交互问答：

```bash
mkdir -p video-ba-pipe && cd video-ba-pipe
curl -fsSLo generate_compose.sh \
  https://raw.githubusercontent.com/zuoa/video-ba-pipe/main/scripts/generate_compose.sh && \
  bash generate_compose.sh
```

远程模式会自动下载所选平台的 Compose 模板和必需配置，最终生成 `docker-compose.yml`、`.env` 以及相关配置目录。生成器默认不包含内置 MQTT Broker，并默认将 `ghcr.io` 替换为南京大学的 `ghcr.nju.edu.cn`；交互问答中可以直接修改这两个选择。Docker Hub 镜像不会被替换。

如果目标机器无法直接访问 `raw.githubusercontent.com`，可以使用 GHProxy。下面的命令不仅通过代理下载生成器，也会让生成器后续下载 Compose 模板和配置文件时继续使用同一代理：

```bash
mkdir -p video-ba-pipe && cd video-ba-pipe && curl -fsSLo generate_compose.sh 'https://gh-proxy.com/https://raw.githubusercontent.com/zuoa/video-ba-pipe/main/scripts/generate_compose.sh' && VIDEO_BA_PIPE_CONFIG_BASE_URL='https://gh-proxy.com/https://raw.githubusercontent.com/zuoa/video-ba-pipe/main' bash generate_compose.sh
```

GHProxy 属于第三方代理服务，可能存在缓存延迟或可用性变化；下载后可以先检查 `generate_compose.sh` 再执行。也可将命令中的 `https://gh-proxy.com/` 替换为兼容“代理地址 + 完整 GitHub URL”格式的自建代理。

也可以在无人值守部署中显式传参：

```bash
./scripts/generate_compose.sh \
  --non-interactive \
  --platform auto \
  --with-mqtt \
  --without-rabbitmq \
  --with-mediamtx \
  --nju-mirror \
  --force
```

`--platform auto` 在 x86 主机上根据 NVIDIA 驱动选择 CPU 或 CUDA，在 ARM64 主机上根据设备树识别 Jetson/Tegra 或 RK3588；也可使用 `cpu`、`cuda`、`jetson`、`rknn` 手动覆盖。启用 `--nju-mirror` 后，仅将 `ghcr.io` 切换到 `ghcr.nju.edu.cn`；PostgreSQL、RabbitMQ、Mosquitto、MediaMTX 等 Docker Hub 镜像保持上游地址。

生成器默认还会根据 `deploy/compose/required-files.txt`，在输出 Compose 文件的同级目录准备所有必需文件：始终准备 `frontend/nginx.conf` 和 `data/`，启用 MQTT 时准备 `deploy/mosquitto.conf`，启用 MediaMTX 时准备 `mediamtx.yml`。仓库内有同版本文件时优先复制，否则从 GitHub 下载；已有文件不会被覆盖。可用 `--force-configs` 强制更新、`--no-download-configs` 完全跳过，或用 `--config-base-url` 指定自建下载源。

交互模式默认询问是否生成 `.env`，随后填写镜像版本、公司名、前端 HTTP 端口（`HTTP_PORT`，默认 `8080`）、PostgreSQL 数据库名/用户名/密码、外部访问地址、节点 ID、设备型号代码等必要变量；JWT 与媒体签名密钥可直接回车自动生成。选择 MediaMTX 或 RabbitMQ 后，还会继续询问对应账号和连接参数。生成的 `.env` 权限为 `600`，已有文件默认不会覆盖。无人值守模式会使用传入的同名环境变量，并为缺失的密码和密钥生成随机值；可用 `--no-env-file` 跳过，或用 `--force-env` 重新生成。全部参数见 `./scripts/generate_compose.sh --help`。

以后更新部署配置时，重新下载最新版生成器并生成即可；已有 `.env` 和配置文件默认保留，只有 `docker-compose.yml` 被更新：

```bash
curl -fsSLo generate_compose.sh https://raw.githubusercontent.com/zuoa/video-ba-pipe/main/scripts/generate_compose.sh && bash generate_compose.sh --force
```

如果确认没有手工修改过 `nginx.conf`、`mosquitto.conf` 或 `mediamtx.yml`，可再加 `--force-configs` 同步这些托管配置；否则先保留并对比，避免覆盖现场定制。

生成文件头会记录平台、可选服务、镜像源和模板来源。生产环境如需严格复现，可把脚本下载 URL 与 `VIDEO_BA_PIPE_CONFIG_BASE_URL` 中的 `main` 同时替换为同一个 tag 或完整 commit SHA。仓库中的模板、片段或必要文件清单发生变化后，GitHub Actions 会生成并校验所有平台的基础/完整组合，避免脚本与部署源不同步。

`ghcr.nju.edu.cn` 是第三方 GHCR 缓存，出现 `502 Bad Gateway` 表示镜像站当时没有成功回源，并不是 Compose 格式错误。此时重新运行生成器并在问答中选择不使用南京大学镜像，或传入 `--no-nju-mirror` 后再执行 `docker compose pull`。

### 1) CPU 部署

```bash
./scripts/generate_compose.sh --non-interactive --platform cpu --force
docker compose up -d
```

消息投递默认关闭。启动后在“系统设置 → 消息投递”中启用并配置 MQTT、RabbitMQ 或 HTTP API；连接参数不使用环境变量。内置 MQTT 地址为 `mqtt:1883`、用户名为 `video-ba`，密码在每个部署首次启动时随机生成并持久化，且默认不向宿主机映射 1883 端口。使用相同 Compose 文件执行以下命令获取密码后填入页面：

```bash
docker compose exec mqtt cat /mosquitto/secrets/initial-password
```

人脸识别的事件保留期、默认推理后端和商用模型门禁同样在“系统设置 → 人脸识别”中管理。只有数据卷路径、生物数据密钥和可信推理插件这类启动级配置保留在部署环境中。
当前 compose 已内置 PostgreSQL，应用默认通过 `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD` 连接数据库。  
如需改用外部 PostgreSQL，可在 `.env` 中覆盖 `DB_HOST`、`DB_PORT`、`DB_NAME`、`DB_USER`、`DB_PASSWORD`。
页面 Header 默认显示公司名称“码全科技”；可在 `.env` 中通过 `COMPANY_NAME` 修改。

### 2) X86 + CUDA/GPU 部署

```bash
./scripts/generate_compose.sh --non-interactive --platform cuda --force
docker compose up -d
```

要求：宿主机已安装 NVIDIA 驱动与 Docker NVIDIA Runtime。

> 注意：CUDA 模板默认 `VIDEO_DECODER_TYPE=ffmpeg_nvdec`（NVDEC 硬解，
> 并发路数由硬解预算器按 NVML 解码利用率自动调节）。
> 如需保持软解，在 `.env` 中设置 `VIDEO_DECODER_TYPE=ffmpeg_sw`。

### 3) RK3588/NPU 部署

```bash
./scripts/generate_compose.sh --non-interactive --platform rknn --force
docker compose up -d
```

说明：RK 部署默认内置 PostgreSQL，并保持 `WEB_CONCURRENCY=1` 作为稳妥默认值，避免盒子上额外放大 API 侧并发压力。
如需启用 RK 硬解，推荐先构建独立的 `ffmpeg+rkmpp` 基础镜像，再由 `Dockerfile.rk` 通过 `COPY --from` 复用；在 CI 中会自动按当前仓库 owner 选择对应的基础镜像，部署时 compose 仍可保持拉取远程镜像。

### 4) Jetson Orin NX Super 16GB 部署

```bash
./scripts/generate_compose.sh --non-interactive --platform jetson --force
docker compose up -d
```

要求：目标设备运行 JetPack 6.2.1 / L4T 36.4.4，并已安装 Docker NVIDIA Runtime。后端使用独立的 `:jetson-<release>` ARM64 worker 镜像，CUDA 推理由 NVIDIA PyTorch iGPU 运行时提供，H.264/H.265 默认使用 `nvv4l2decoder` 硬解；启动视频源时会用 `ffprobe` 探测并保存实际编码。前端复用通用 `:arm64` 镜像。Super 功耗模式需在宿主机刷机和 `nvpmodel` 配置中启用。

所有 Compose 部署都会先运行一次性 `db-init` 服务。该服务完成事务化数据库迁移后，API、jobs 和 worker 才会启动；迁移失败时业务容器保持停止，避免在不完整 schema 上继续提供服务。API、db-init 和 jobs 共用平台对应的轻量 `:control-<platform>-<release>` 镜像，只有 worker 拉取 CPU/CUDA/Jetson/RK 推理框架。control 与 worker 由同一个工作流按同一 commit 成对发布；生产环境建议在 `.env` 中设置 `VIDEO_BA_PIPE_RELEASE=<完整 commit SHA>`。普通应用更新通常只变化轻量代码层，不再重复下载 GB 级 PyTorch、Paddle 或平台运行时层。排查部署门禁可使用：

```bash
docker compose ps
docker compose logs db-init
```

### 可选服务

不再维护成组的 `no-mqtt` 副本。MQTT 默认关闭；如果使用外部 MQTT Broker，或改用 RabbitMQ/HTTP，直接使用默认生成结果即可。如需内置服务，通过生成参数选择：

```bash
./scripts/generate_compose.sh --with-mqtt --with-mediamtx --force
```

可选项为 `--with-mqtt`、`--with-rabbitmq` 和 `--with-mediamtx`，每项都可独立开启；对应的 `--without-*` 参数用于无人值守脚本中显式关闭。

## 访问地址

- 前端：`http://localhost:${HTTP_PORT}`（默认 `8080`）
- 后端 API：`http://localhost:5002`
- RabbitMQ 管理台（启用时）：`http://localhost:15672`（账号密码见 `.env`）

## 本地开发（非 Docker）

### 后端

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 本地直跑默认使用 SQLite；必须先完成数据库初始化
python3 -m app.setup_database

# 终端 1：启动后台任务
python3 -m app.jobs

# 终端 2：启动 worker
python app/main.py

# 终端 3：启动 API
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
- `RECORDING_ENABLED`：是否录制预警视频（默认关闭；运行后优先使用“系统设置”中的配置）
- `ALERT_VIDEO_MAX_STORAGE_GB`：本地告警录像容量上限，默认 20 GB
- `ALERT_IMAGE_MAX_STORAGE_GB`：本地告警图片容量上限，默认 10 GB
- `ALERT_IMAGE_MIN_FREE_GB`：磁盘最低剩余空间，默认 10 GB
- `VIDEO_DECODER_TYPE`：默认视频解码器类型；RK3588 推荐 `rk_mpp`，Jetson 推荐 `jetson_gst`
- `FFMPEG_DIRECT_RTSP_ENABLED`：FFmpeg 软解、NVDEC 和 RKMPP 是否直接拉取 RTSP 并解码；默认 `true`，异常时会自动回退两阶段链路
- `ANALYSIS_TARGET_FPS` / `ANALYSIS_BUFFER_SECONDS`：分析链路缓冲参数
- `PRE_ALERT_DURATION` / `POST_ALERT_DURATION` / `RECORDING_BUFFER_DURATION`：录制链路缓冲参数

运行后可在“系统设置”中配置磁盘压力保护和钉钉运维通知：默认磁盘使用率达到 80% 时停止正在进行及后续告警录像，达到 90% 时只创建告警元数据、不再写入图片或录像。媒体清理按最老文件优先覆盖；磁盘水位变化、清理失败以及指定时间窗内告警量超过阈值时，可通过钉钉群自定义机器人 Webhook 通知，并按冷却时间去重。
- `RECORDING_JPEG_QUALITY` / `RECORDING_COMPRESSED_MAX_BYTES`：录制压缩帧缓存参数
- `IS_EXTREME_DECODE_MODE`：极速解码（仅保留最新帧）
- `RESOURCE_PROFILING_ENABLED`：输出帧拷贝、录制编码、工作流执行等性能埋点
- `WORKFLOW_ZERO_COPY_FRAMES`：source host 使用共享内存只读视图读取最新帧，减少复制（需确保处理耗时小于缓冲窗口）
- `SOURCE_HOST_WORKFLOW_NODE_WORKERS`：实时工作流同层节点并行 worker 数，`0` 表示关闭
- MQTT / RabbitMQ / HTTP 连接参数仅通过“系统设置 → 消息投递”配置

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
├── deploy/compose/       # 平台模板、可选服务片段和必要文件清单
├── scripts/generate_compose.sh
├── docker-compose.yml    # 本地生成产物（不提交）
└── env.example
```

## 相关文档

- 组合检测画布与兼容配置：`docs/cascade_detection.md`
- Docker 镜像构建说明：`docs/docker_build_workflows.md`
- Jetson Orin NX Super 镜像与部署：`docs/jetson_orin_nx_docker.md`
- RK3588 镜像与构建说明：`docs/rk3588_docker.md`
- RK3588 板端部署/排障：`docs/rk_usage_manual.md`
- MQTT / RabbitMQ / HTTP 消息格式与接入：`docs/message_queue_integration.md`
- 前端说明：`frontend/README.md`
