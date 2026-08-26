# Docker 镜像构建说明

本文说明本项目 Docker 镜像的构建维度、GitHub Actions 入口和本地构建命令。

## 两个维度

镜像构建分为两个独立维度：

1. 平台架构（platform）
   - `linux/amd64`：x86 服务器、普通云主机、NVIDIA GPU 服务器
   - `linux/arm64`：RK3588、Jetson Orin 等 ARM64 设备

2. 后端运行时（runtime）
   - `control`：API、数据库迁移和后台 jobs，不包含推理框架，使用 `Dockerfile.control`
   - `cpu`：x86 CPU 推理，使用 `Dockerfile.cpu`
   - `cuda`：x86 NVIDIA GPU 推理（NVDEC 硬解），使用 `Dockerfile.cuda`
   - `rk`：RK3588 / NPU 推理，使用 `Dockerfile.rk`
   - `jetson`：Jetson Orin / CUDA 推理与硬解，使用 `Dockerfile.jetson`

注意：`amd64` 和 `cuda` 不是同一维度。`amd64` 是平台架构，`cuda` 是后端运行时。

## GitHub Actions

### 后端镜像

`Build control image` 在 `main` 相关文件变化时构建多架构轻量制品，但不推进任何部署标签。
各平台发布工作流会从同一个 checkout 同时构建 control 与 worker，确认两者成功后才推进
该平台的 `*-stable` 标签。同一平台的发布任务使用共享串行锁，独立工作流也不会交错
改写这两个标签。CPU worker 由 `Build backend images` 构建。

手动触发参数：

| 参数 | 说明 |
| --- | --- |
| `runtime=cpu` | 构建 x86 CPU 后端镜像 |
| `runtime=jetson` | 构建 Jetson Orin ARM64 后端镜像 |
| `runtime=all` | 同时构建 CPU、Jetson 后端镜像 |

X86+CUDA 与 RKNN 镜像分别由独立工作流 `Build X86+CUDA image`、`Build RKNN image`
手动触发构建，不在 `Build backend images` 的 `runtime` 选项中。

产物：

| runtime | platform | Dockerfile | 镜像 tag |
| --- | --- | --- | --- |
| `control-cpu` | `linux/amd64` | `Dockerfile.control` | `ghcr.io/<owner>/<repo>:control-cpu-<commit>`、`:control-cpu-stable` |
| `cpu` | `linux/amd64` | `Dockerfile.cpu` | `ghcr.io/<owner>/<repo>:cpu-<commit>`、`:cpu-stable` |
| `control-cuda` / `cuda` | `linux/amd64` | `Dockerfile.control` / `Dockerfile.cuda` | `:control-cuda-<commit>` / `:cuda-<commit>`（以及对应 `-stable`） |
| `control-rk` / `rk` | `linux/arm64` | `Dockerfile.control` / `Dockerfile.rk` | `:control-rk-<commit>` / `:rk-<commit>`（以及对应 `-stable`） |
| `control-jetson` / `jetson` | `linux/arm64` | `Dockerfile.control` / `Dockerfile.jetson` | `:control-jetson-<commit>` / `:jetson-<commit>`（以及对应 `-stable`） |

完整 commit tag 是不可变部署标识。生产环境在 `.env` 设置
`VIDEO_BA_PIPE_RELEASE=<完整 commit SHA>` 后，Compose 会为 control 和 worker 使用同一个后缀。
不设置时使用成对推进的 `stable`，适合跟随对应平台的最近一次成功发布。不要分别覆盖两个
不同提交的 `VIDEO_BA_PIPE_CONTROL_IMAGE` 和 worker 镜像变量。

每个 Dockerfile 都把依赖阶段命名为 `runtime-base`。CI 同时发布稳定的
`:runtime-control`、`:runtime-cpu`、`:runtime-cuda`、`:runtime-jetson` 和
`:runtime-rk`，并将 BuildKit 缓存写入 GHCR 的 `:buildcache-*` 引用。版本号和构建时间
只注入最终应用层，因此普通代码发布不会让重型依赖层失效。control 镜像还会执行
1 GiB 上限检查，并在构建阶段断言没有 Torch、Ultralytics、ONNX Runtime、Paddle 或 RKNN。

push 到 `main` 时，后端 workflow 默认只自动构建 `runtime=cpu`。Jetson 镜像通过
`Build backend images`（或 `Build Jetson image`）手动触发构建；X86+CUDA 与 RKNN 镜像分别通过
`Build X86+CUDA image`、`Build RKNN image` 手动触发构建。

### 应用版本和构建时间

所有最终应用镜像在 GitHub Actions 构建时都会自动生成以下镜像内环境变量：

- `APP_VERSION`：最近 Git 提交的日期和 7 位 commit hash，格式为
  `YYYYMMDD-<commit>`，例如 `20260813-7b59fd9`
- `BUILD_TIME`：UTC ISO 8601 时间，例如 `2026-08-12T04:30:15Z`

Git 本身不记录 push 时间，因此版本日期使用最新提交的 committer 时间；日期按该提交记录的
时区生成。`BUILD_TIME` 继续记录镜像的实际构建时间。

相同的值也会写入 OCI 标签 `org.opencontainers.image.version` 和
`org.opencontainers.image.created`。运行中的应用继续读取 `APP_VERSION`，因此系统信息接口和
页面版本号不再依赖部署机器额外配置环境变量。

可以直接检查已发布镜像中的值：

```bash
docker image inspect ghcr.io/<owner>/<repo>:cpu-<commit> \
  --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^(APP_VERSION|BUILD_TIME)='
```

本地手工构建前，可用同一个生成器生成构建参数：

```bash
eval "$(python3 scripts/generate_docker_build_info.py --format shell)"
```

### 前端镜像

工作流：`Build frontend images`

手动触发参数：

| 参数 | 说明 |
| --- | --- |
| `platform=linux/amd64` | 构建 x86 前端镜像 |
| `platform=linux/arm64` | 构建通用 ARM64 前端镜像 |
| `platform=all` | 同时构建两个平台的前端镜像 |

产物：

| platform | Dockerfile | 镜像 tag |
| --- | --- | --- |
| `linux/amd64` | `frontend/Dockerfile` | `ghcr.io/<owner>/<repo>-frontend:main` |
| `linux/arm64` | `frontend/Dockerfile.rk` | `ghcr.io/<owner>/<repo>-frontend:arm64`、`:rk` |

push 到 `main` 且前端相关文件变化时，会自动构建 x86 和 ARM64 前端镜像。

### RK FFmpeg 基础镜像

工作流：`Build and publish RK3588 FFmpeg image`

这个 workflow 只用于 RK3588，不用于 x86。它构建 `Dockerfile.ffmpeg.rk`，产物默认是：

```text
ghcr.io/<owner>/video-ba-pipe-ffmpeg-rk:rkmpp
```

RK 后端镜像会通过 `FFMPEG_RK_IMAGE` 复用这个基础镜像里的 `/opt/ffmpeg`。

## x86 构建

### GitHub Actions

后端 CPU：

```text
Build backend images -> runtime=cpu
```

后端 X86+CUDA：

```text
Build X86+CUDA image -> Run workflow
```

前端：

```text
Build frontend images -> platform=linux/amd64
```

x86 不需要运行 `Build and publish RK3588 FFmpeg image`。

### 本地构建

control（本机架构）：

```bash
docker buildx build \
  -f Dockerfile.control \
  --build-arg "APP_VERSION=$app_version" \
  --build-arg "BUILD_TIME=$build_time" \
  -t video-ba-pipe:control \
  --load \
  .
```

后端 CPU：

```bash
docker buildx build --platform=linux/amd64 \
  -f Dockerfile.cpu \
  --build-arg "APP_VERSION=$app_version" \
  --build-arg "BUILD_TIME=$build_time" \
  -t video-ba-pipe:cpu \
  --load \
  .
```

后端 CUDA：

```bash
docker buildx build --platform=linux/amd64 \
  -f Dockerfile.cuda \
  --build-arg "APP_VERSION=$app_version" \
  --build-arg "BUILD_TIME=$build_time" \
  -t video-ba-pipe:cuda \
  --load \
  .
```

前端：

```bash
docker buildx build --platform=linux/amd64 \
  -f frontend/Dockerfile \
  -t video-ba-pipe-frontend:main \
  --load \
  ./frontend
```

## RK3588 构建

推荐顺序：

1. 运行 `Build and publish RK3588 FFmpeg image` 构建 RK FFmpeg 基础镜像。
2. 运行 `Build RKNN image` 构建 RK 后端镜像（可填写 torch/onnxruntime/rknn wheel 与 FFmpeg RK 基础镜像参数）。
3. 运行 `Build frontend images`，选择 `platform=linux/arm64` 构建 RK 前端镜像。

RK 的 wheel、FFmpeg 包、NPU runtime 挂载等细节见 `docs/rk3588_docker.md`。

## Jetson Orin 构建

目标基线为 JetPack 6.2.1 / L4T 36.4.4。

### GitHub Actions

```text
Build Jetson image
```

该工作流会先构建并推送 Jetson 后端镜像，成功后再自动构建同一提交的
ARM64 前端镜像。

### 本地构建

```bash
docker buildx build --platform=linux/arm64 \
  -f Dockerfile.jetson \
  --build-arg "APP_VERSION=$app_version" \
  --build-arg "BUILD_TIME=$build_time" \
  -t video-ba-pipe:jetson \
  --load \
  .
```

镜像默认基于 `nvcr.io/nvidia/pytorch:25.05-py3-igpu`。如需从内部镜像仓库中转，可通过 `JETSON_PYTORCH_IMAGE` build arg 覆盖，但替代镜像仍须兼容 JetPack 6.2。

## 部署对应关系

| 部署方式 | control（db-init/api/jobs） | worker | 前端镜像 | 生成参数 |
| --- | --- | --- | --- | --- |
| x86 CPU | `ghcr.io/<owner>/<repo>:control-cpu-<release>` | `ghcr.io/<owner>/<repo>:cpu-<release>` | `ghcr.io/<owner>/<repo>-frontend:main` | `--platform cpu` |
| x86 CUDA | `ghcr.io/<owner>/<repo>:control-cuda-<release>` | `ghcr.io/<owner>/<repo>:cuda-<release>` | `ghcr.io/<owner>/<repo>-frontend:main` | `--platform cuda` |
| RK3588 | `ghcr.io/<owner>/<repo>:control-rk-<release>` | `ghcr.io/<owner>/<repo>:rk-<release>` | `ghcr.io/<owner>/<repo>-frontend:rk` | `--platform rknn` |
| Jetson Orin NX | `ghcr.io/<owner>/<repo>:control-jetson-<release>` | `ghcr.io/<owner>/<repo>:jetson-<release>` | `ghcr.io/<owner>/<repo>-frontend:arm64` | `--platform jetson` |

使用 `scripts/generate_compose.sh` 生成统一的 `docker-compose.yml`；可选服务通过 `--with-mqtt`、`--with-rabbitmq`、`--with-mediamtx` 添加。
