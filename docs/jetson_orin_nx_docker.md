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
Build Jetson image
```

工作流会先发布后端：

```text
ghcr.io/<owner>/<repo>:jetson
ghcr.io/<owner>/<repo>:jetson-<commit>
```

随后自动发布同一提交的 ARM64 前端：

```text
ghcr.io/<owner>/<repo>-frontend:arm64
ghcr.io/<owner>/<repo>-frontend:arm64-<commit>
```

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

需要跟随 GitHub Actions 最新构建时，应使用上面的 `:jetson` 和 `:arm64`
标签，不要在 `.env` 中固定旧的 `@sha256:...` digest。Action 只负责推送镜像，
不会自动重建 Jetson 上已经运行的容器。

启动或更新：

```bash
docker compose -f docker-compose.yml.jetson pull
docker compose -f docker-compose.yml.jetson up -d --force-recreate
docker compose -f docker-compose.yml.jetson ps
```

确认前端大文件上传限制已经加载：

```bash
docker compose -f docker-compose.yml.jetson exec frontend \
  nginx -T 2>&1 | grep client_max_body_size
```

预期输出为 `client_max_body_size 1g;`。Jetson compose 还包含相同规则的
启动校验和健康检查，配置缺失时前端不会被标记为 healthy。

默认配置：

- `VIDEO_DECODER_TYPE=jetson_gst`
- `VIDEO_FRAME_PIXEL_FORMAT=nv12`
- API：`http://localhost:5002`
- 前端：`http://localhost:8080`
- RabbitMQ 管理台：`http://localhost:15672`

可用的 Jetson 解码器名称为 `jetson_gst`，兼容别名为 `jetson` 和 `nvv4l2`。不支持的编码或缺失 NVIDIA GStreamer 插件时，decoder worker 会直接报错退出，不会静默切换到 CPU。

视频源每次启动前会通过 `ffprobe` 探测实际编码，将结果保存到 `source_codec`，并把同一个编码值同时传给 FFmpeg 拉流封装和 Jetson parser。历史数据库会在启动时自动增加该字段；探测临时失败时仅可回退到该视频源上一次成功保存的结果，首次探测失败不会盲猜 H.264。

## 硬解资源准入与重启退避（多路并发保护）

NVDEC 的 DMA 缓冲区来自宿主机 CMA（默认 256MB），每路 `nvv4l2decoder` 实例约占用 12MB。系统常驻（GPU/显示等）再占去约 130MB，默认只能支撑约 7-8 路硬解。此前超出容量的解码进程会进入 "Host1x channel open failed → 堆破坏 SIGABRT → 立即重启" 的崩溃循环。系统内置了与路数无关的保护机制，无需按视频源数量手工调参：

- **自适应硬解准入**：orchestrator 按 `/proc/meminfo` 的 CMA 容量自动计算硬解并发槽位（`HW_DECODE_CMA_*` 可调），只向拿到槽位的源发放硬解；出现资源类失败自动降档，稳定运行后缓慢升档试探真实容量。
- **自动软解兜底**：拿不到槽位的源自动改用 `ffmpeg_sw` 软解（`HW_DECODE_SW_FALLBACK_*` 控制，可用 `HW_DECODE_SW_FALLBACK_MAX` 限制软解路数保护 CPU）；硬解槽位空闲后每 60s 自动把一路软解源升级回硬解。
- **失败分类退避**：解码进程退出按 stderr/退出码分类为 resource/stream/crash，分别走 15s/30s/5s 起始的指数退避（上限 `SOURCE_RESTART_BACKOFF_MAX_SECONDS`，默认 300s），期间视频源状态为 `ERROR` 并记录健康事件（`restart_backoff`/`resource_wait`/`sw_fallback`/`hw_upgrade`）。上游流不存在（如 RTSP 404）不再每秒空转重启。
- **启动限流**：每个管理周期最多启动 `SOURCE_MAX_CONCURRENT_STARTS`（默认 2）个源，防止容器启动时 ffprobe/NVDEC 通道惊群。
- **僵尸回收**：worker/api 容器启用 `init: true`，orchestrator 每 10s 主动回收僵尸子进程。

CMA 余量可在系统指标 API（`memory.cma_free_mb`）中观测。

### 大容量部署建议（可选）

路数较多且希望全部走硬解时，可在宿主机扩大 CMA（默认 256MB → 768MB）：

```bash
sudo cp /boot/extlinux/extlinux.conf /boot/extlinux/extlinux.conf.bak
sudo sed -i 's/console=tty0$/console=tty0 cma=768M/' /boot/extlinux/extlinux.conf
grep APPEND /boot/extlinux/extlinux.conf   # 确认 cma=768M 已追加
sudo reboot
```

不调整内核参数系统也能稳定运行（超出部分自动软解），该参数只是提升硬解容量上限。

## 共享推理与 OOM 保护

Jetson compose 仅在 worker 容器中默认启用本机共享推理、推理内存准入和
OOM 熔断。使用 `templates/adaptive_yolo_detector.py` 且实际选择 Ultralytics
后端时，相同模型由独立模型进程加载一次，各 source host 通过 Unix socket
和 POSIX shared memory 提交帧；队列满时丢弃分析帧，不继续扩张内存。

`simple_yolo_detector.py`、`yolo_detector.py` 以及直接实例化
`ultralytics.YOLO` 的自定义脚本仍在各 source host 内加载模型。准入控制会将
这些模型按 host 和模型配置项逐份计算，不会把同一模型 ID 误判为已共享。
API 容器保持共享推理关闭，因此算法测试接口不会连接 worker 私有的 `/tmp`
socket。

关键默认配置：

```text
SHARED_INFERENCE_ENABLED=true
SHARED_INFERENCE_QUEUE_SIZE=2
SHARED_INFERENCE_BATCH_MAX_SIZE=4
INFERENCE_ADMISSION_ENABLED=true
INFERENCE_SYSTEM_RESERVE_MB=2048
OOM_CIRCUIT_BREAKER_ENABLED=true
OOM_CIRCUIT_FAILURE_THRESHOLD=3
```

worker 每 30 秒输出一条 `共享推理资源` 和 `Source host 资源` 日志，可检查
模型进程数量、PSS、Swap、引用数与队列深度。若 source host 被全局 OOM
killer 终止，日志会显示 `workflow_oom_backoff` 或
`workflow_oom_circuit_open`，熔断期间不会立即重新加载模型。

紧急回退到旧的每工作流本地模型方式时可设置
`SHARED_INFERENCE_ENABLED=false`；该模式内存开销较大，不建议在多路 Jetson
部署中长期使用。

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
