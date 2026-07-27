# Jetson Orin NX Super 16GB 镜像与部署

本文说明 Jetson Orin NX Super 16GB 的独立构建、部署和实机验收流程。

## 支持基线

- 设备：Jetson Orin NX 16GB
- JetPack：6.2.1
- Jetson Linux / L4T：36.4.4
- 容器架构：`linux/arm64`
- 后端镜像：`ghcr.io/<owner>/<repo>:jetson`
- 前端镜像：`ghcr.io/<owner>/<repo>-frontend:arm64`
- CUDA/PyTorch 基座：`nvcr.io/nvidia/pytorch:25.05-py3-igpu`
- 视频硬解：GStreamer `nvv4l2decoder`，支持 H.264/H.265 Annex-B 字节流

JetPack 6.2.1 和 L4T 36.4.4 的对应关系见 [NVIDIA JetPack 6.2.1](https://developer.nvidia.com/embedded/jetpack-sdk-621)。PyTorch 25.05 iGPU 容器与 JetPack 6.2 的兼容关系见 [NVIDIA PyTorch for Jetson compatibility](https://docs.nvidia.com/deeplearning/frameworks/install-pytorch-jetson-platform-release-notes/pytorch-jetson-rel.html)。

该镜像不用于 x86 NVIDIA GPU，也不用于 RK3588。JetPack/L4T 主版本不同的设备需要单独验证和打包。

## 宿主机准备

确认设备版本：

```bash
head -n 1 /etc/nv_tegra_release
sudo nvpmodel -q
docker info | grep -i runtime
```

`/etc/nv_tegra_release` 应显示 36.4 系列。Docker 必须能使用 `nvidia` runtime。

Orin NX Super 模式属于宿主机 BSP 和功耗配置。设备必须使用支持 Super 的刷机配置，并由运维根据散热、电源条件选择相应 `nvpmodel` 模式；容器不会修改宿主机功耗模式。

## 构建和发布

在 GitHub Actions 中运行：

```text
Build backend images -> runtime=jetson
Build frontend images -> platform=linux/arm64
```

后端会发布：

```text
ghcr.io/<owner>/<repo>:jetson
ghcr.io/<owner>/<repo>:jetson-<commit>
```

ARM64 前端同一镜像会发布 `arm64` 和兼容旧部署的 `rk` 两组标签。

本地构建：

```bash
docker buildx build --platform=linux/arm64 \
  -f Dockerfile.jetson \
  -t video-ba-pipe:jetson \
  --load \
  .
```

如需使用内部缓存的 NGC 镜像：

```bash
docker buildx build --platform=linux/arm64 \
  -f Dockerfile.jetson \
  --build-arg JETSON_PYTORCH_IMAGE=<registry>/nvidia-pytorch:25.05-py3-igpu \
  -t video-ba-pipe:jetson \
  --load \
  .
```

## 部署

如镜像不在默认的 `ghcr.io/zuoa` 路径，先在 `.env` 中覆盖：

```dotenv
VIDEO_BA_PIPE_JETSON_IMAGE=ghcr.io/<owner>/<repo>:jetson
VIDEO_BA_PIPE_FRONTEND_IMAGE=ghcr.io/<owner>/<repo>-frontend:arm64
```

启动：

```bash
docker compose -f docker-compose.yml.jetson up -d
docker compose -f docker-compose.yml.jetson ps
```

默认配置：

- `VIDEO_DECODER_TYPE=jetson_gst`
- `VIDEO_FRAME_PIXEL_FORMAT=nv12`
- API：`http://localhost:5002`
- 前端：`http://localhost:8080`
- RabbitMQ 管理台：`http://localhost:15672`

可用的 Jetson 解码器名称为 `jetson_gst`，兼容别名为 `jetson` 和 `nvv4l2`。不支持的编码或缺失 NVIDIA GStreamer 插件时，decoder worker 会直接报错退出，不会静默切换到 CPU。

视频源每次启动前会通过 `ffprobe` 探测实际编码，将结果保存到 `source_codec`，并把同一个编码值同时传给 FFmpeg 拉流封装和 Jetson parser。历史数据库会在启动时自动增加该字段；探测临时失败时仅可回退到该视频源上一次成功保存的结果，首次探测失败不会盲猜 H.264。

## 实机验收

先验证 CUDA、运行时库和插件：

```bash
docker compose -f docker-compose.yml.jetson exec worker \
  /app/scripts/verify_jetson_runtime.sh
```

如有裸 H.264/H.265 测试码流放在 `./data` 下，可同时做真实硬解检查：

```bash
docker compose -f docker-compose.yml.jetson exec worker \
  /app/scripts/verify_jetson_runtime.sh /data/sample.h264 h264
```

随后在系统中配置一个 H.264 和一个 H.265 视频源，确认：

1. worker 日志包含 `Jetson hardware decoder started`。
2. 视频源持续产帧，没有 `nvv4l2decoder` 或 CUDA 初始化错误。
3. 算法推理使用 CUDA，能够完成检测、告警、截图和录像。
4. compose 重启后数据库和 `/data` 内容保留。

查看日志：

```bash
docker compose -f docker-compose.yml.jetson logs -f worker
docker compose -f docker-compose.yml.jetson logs -f api
```

## 范围限制

- 当前包只保证 JetPack 6.2.1 / L4T 36.4.4。
- 首版不包含 TensorRT engine 转换或 DLA 调度。
- GStreamer 硬解支持 H.264/H.265；MJPEG 请显式改用软件解码器。
- GitHub 托管 runner 只负责 ARM64 构建，CUDA和硬解必须在 Jetson 实机验收。
