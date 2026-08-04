# Docker 镜像构建说明

本文说明本项目 Docker 镜像的构建维度、GitHub Actions 入口和本地构建命令。

## 两个维度

镜像构建分为两个独立维度：

1. 平台架构（platform）
   - `linux/amd64`：x86 服务器、普通云主机、NVIDIA GPU 服务器
   - `linux/arm64`：RK3588、Jetson Orin 等 ARM64 设备

2. 后端运行时（runtime）
   - `cpu`：x86 CPU 推理，使用 `Dockerfile.cpu`
   - `cuda`：x86 NVIDIA GPU 推理（NVDEC 硬解），使用 `Dockerfile.cuda`
   - `rk`：RK3588 / NPU 推理，使用 `Dockerfile.rk`
   - `jetson`：Jetson Orin / CUDA 推理与硬解，使用 `Dockerfile.jetson`

注意：`amd64` 和 `cuda` 不是同一维度。`amd64` 是平台架构，`cuda` 是后端运行时。

## GitHub Actions

### 后端镜像

工作流：`Build backend images`

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
| `cpu` | `linux/amd64` | `Dockerfile.cpu` | `ghcr.io/<owner>/<repo>:cpu` |
| `cuda`(独立工作流) | `linux/amd64` | `Dockerfile.cuda` | `ghcr.io/<owner>/<repo>:cuda` |
| `rk`(独立工作流) | `linux/arm64` | `Dockerfile.rk` | `ghcr.io/<owner>/<repo>:rk` |
| `jetson` | `linux/arm64` | `Dockerfile.jetson` | `ghcr.io/<owner>/<repo>:jetson` |

每个镜像还会额外推送一个带 commit 的 tag，例如 `cpu-<commit>`。

push 到 `main` 时，后端 workflow 默认只自动构建 `runtime=cpu`。Jetson 镜像通过
`Build backend images`（或 `Build Jetson image`）手动触发构建；X86+CUDA 与 RKNN 镜像分别通过
`Build X86+CUDA image`、`Build RKNN image` 手动触发构建。

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

后端 CPU：

```bash
docker buildx build --platform=linux/amd64 \
  -f Dockerfile.cpu \
  -t video-ba-pipe:cpu \
  --load \
  .
```

后端 CUDA：

```bash
docker buildx build --platform=linux/amd64 \
  -f Dockerfile.cuda \
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
  -t video-ba-pipe:jetson \
  --load \
  .
```

镜像默认基于 `nvcr.io/nvidia/pytorch:25.05-py3-igpu`。如需从内部镜像仓库中转，可通过 `JETSON_PYTORCH_IMAGE` build arg 覆盖，但替代镜像仍须兼容 JetPack 6.2。

## 部署对应关系

| 部署方式 | 后端镜像 | 前端镜像 | compose 文件 |
| --- | --- | --- | --- |
| x86 CPU | `ghcr.io/<owner>/<repo>:cpu` | `ghcr.io/<owner>/<repo>-frontend:main` | `docker-compose.yml` |
| x86 CUDA | `ghcr.io/<owner>/<repo>:cuda` | `ghcr.io/<owner>/<repo>-frontend:main` | `docker-compose.yml.x86+cuda` |
| RK3588 | `ghcr.io/<owner>/<repo>:rk` | `ghcr.io/<owner>/<repo>-frontend:rk` | `docker-compose.yml.rknn` |
| Jetson Orin NX | `ghcr.io/<owner>/<repo>:jetson` | `ghcr.io/<owner>/<repo>-frontend:arm64` | `docker-compose.yml.jetson` |
