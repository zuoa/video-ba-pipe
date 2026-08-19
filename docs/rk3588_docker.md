# RK3588 Docker 镜像构建与运行

本项目提供两层 RK 镜像：

- `Dockerfile.ffmpeg.rk`：仅构建 `ffmpeg/ffprobe` 及运行库，供业务镜像复用
- `Dockerfile.rk`：业务镜像，使用 `COPY --from=<ffmpeg base image>` 复用 RK FFmpeg

板端部署与网络问题处理请参考：`docs/rk_usage_manual.md`

通用 Docker 镜像构建入口、x86/RK 构建维度说明请参考：`docs/docker_build_workflows.md`

数据库说明：
- RK compose 已内置 PostgreSQL 服务。
- 应用容器通过 `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD` 连接数据库。
- 如需迁移旧 SQLite 数据，可在 PostgreSQL 启动后执行 `docker compose -f docker-compose.yml.rknn run --rm -v ./data:/data api python /app/scripts/migrate_sqlite_to_postgres.py --sqlite-path /data/db/ba.db`。

## GitHub Actions 手动构建

工作流：

- `Build and publish RK3588 FFmpeg image`
- `Build backend images`，手动触发时选择 `runtime=rk`

推荐顺序：
1. 先构建 FFmpeg 基础镜像
2. 再构建业务镜像

### FFmpeg 基础镜像

工作流：`Build and publish RK3588 FFmpeg image`

参数：
1. `image_name`：默认 `video-ba-pipe-ffmpeg-rk`
2. `tag`：默认 `rkmpp`
3. `ffmpeg_rk_package`：可选。用于指定预编译 `ffmpeg+rkmpp` 包的 URL。留空时会优先查找仓库内 `vendor/ffmpeg/` 中的归档文件，再回退到 Debian 官方 `ffmpeg`（仅软解）。

说明：
- 该 workflow 只构建 `linux/arm64` 镜像。
- 该镜像只提供 `/opt/ffmpeg` 运行时，不包含业务代码和 Python 环境。
- 推荐将业务镜像中的 `FFMPEG_RK_IMAGE` 指向这个镜像。
- workflow 固定使用 `Dockerfile.ffmpeg.rk`，不会再通过手工输入切换到其他 Dockerfile。
- workflow 会同时推送手工指定 tag 和 `sha-<commit>` tag，便于核对镜像是否来自目标提交。

### 业务镜像

参数：
1. `runtime`：选择 `rk`，或选择 `all` 同时构建 CPU/CUDA/RK 后端镜像。
2. `torch_whl`：可选。用于指定 aarch64 版 PyTorch wheel 的 URL 或路径。留空则跳过安装。
3. `onnxruntime_whl`：可选。用于指定 aarch64 版 ONNX Runtime wheel 的 URL 或路径。留空则跳过安装。
4. `ffmpeg_rk_image`：可选。用于指定 FFmpeg 基础镜像。留空时，workflow 会自动使用 `ghcr.io/<repo_owner>/video-ba-pipe-ffmpeg-rk:rkmpp`。

说明：
- 该 workflow 只构建 `linux/arm64` 镜像。
- workflow 固定使用 `Dockerfile.rk`，并推送 `rk` 和 `rk-<commit>` 两个 tag。
- 镜像固定内置 Rockchip 官方 `rknn-toolkit-lite2 2.3.2` 和匹配的 `librknnrt.so 2.3.2`。
- `Dockerfile.rk` 不再自行处理 FFmpeg 包，而是通过 `COPY --from=${FFMPEG_RK_IMAGE}` 获取 `/opt/ffmpeg`。
- `Dockerfile.rk` 内置默认值为本地镜像名 `video-ba-pipe-ffmpeg-rk:rkmpp`，便于离线/本地联调；GitHub Actions 会在构建时自动覆盖为当前仓库 owner 对应的 GHCR 镜像。
- 业务代码侧已支持 `VIDEO_DECODER_TYPE=rk_mpp`，但仅当镜像内 `ffmpeg -decoders` 能看到 `rkmpp` 时才应启用；否则请保持默认软解。

## 本地构建（可选）

默认行为：
- RK 镜像构建时会从 Rockchip 官方 v2.3.2 tag 下载并校验 `rknn-toolkit-lite2` CPython 3.11 ARM64 wheel 与 `librknnrt.so`。
- 两个文件均使用固定 SHA-256 校验，任一内容不匹配时构建会直接失败。
- `Dockerfile.ffmpeg.rk` 可通过 `FFMPEG_RK_PACKAGE` 或 `vendor/ffmpeg/` 注入预编译 `ffmpeg+rkmpp` 包。
- `Dockerfile.rk` 通过 `FFMPEG_RK_IMAGE` 复用已发布的 FFmpeg 基础镜像。
- 当前 `Dockerfile.rk` 使用 Python 3.11，因此 wheel 也需要与 `cp311` 匹配。

本地构建业务镜像前先生成版本和构建时间参数（CI 中会自动执行）：

```bash
eval "$(python3 scripts/generate_docker_build_info.py --format shell)"
```

```bash
docker buildx build --platform=linux/arm64 \
  -f Dockerfile.ffmpeg.rk \
  -t ghcr.io/<org>/video-ba-pipe-ffmpeg-rk:rkmpp \
  --build-arg FFMPEG_RK_PACKAGE=<ffmpeg_rkmpp_tarball_url> \
  .

docker buildx build --platform=linux/arm64 \
  -f Dockerfile.rk \
  -t ghcr.io/<org>/<repo>:rk \
  --build-arg "APP_VERSION=$app_version" \
  --build-arg "BUILD_TIME=$build_time" \
  --build-arg FFMPEG_RK_IMAGE=ghcr.io/<org>/video-ba-pipe-ffmpeg-rk:rkmpp \
  .
```

如需预装 PyTorch（aarch64 wheel）：

```bash
docker buildx build --platform=linux/arm64 \
  -f Dockerfile.rk \
  -t ghcr.io/<org>/<repo>:rk \
  --build-arg "APP_VERSION=$app_version" \
  --build-arg "BUILD_TIME=$build_time" \
  --build-arg TORCH_WHL=<torch_wheel_url_or_path> \
  .
```

如需预装 ONNX Runtime（aarch64 wheel）：

```bash
docker buildx build --platform=linux/arm64 \
  -f Dockerfile.rk \
  -t ghcr.io/<org>/<repo>:rk \
  --build-arg "APP_VERSION=$app_version" \
  --build-arg "BUILD_TIME=$build_time" \
  --build-arg ONNXRUNTIME_WHL=<onnxruntime_wheel_url_or_path> \
  .
```

说明：
- 推荐将预编译 FFmpeg 包放在仓库的 `vendor/ffmpeg/` 目录下，或通过 `FFMPEG_RK_PACKAGE` 指向可下载 URL。
- FFmpeg 包建议解压后包含 `bin/ffmpeg`、`bin/ffprobe`，以及需要的 `lib/` 运行库。
- `Dockerfile.ffmpeg.rk` 会在构建阶段验证 `ffmpeg/ffprobe` 的共享库依赖；如果预编译包缺少 `libav*.so` 等运行库，构建会直接失败，而不是生成无法运行的镜像。
- FFmpeg 基础镜像构建完成后，可在镜像内通过 `ffmpeg -decoders | grep rkmpp` 验证是否带有 RK 硬解能力。
- 业务镜像在 CI 中默认引用 `ghcr.io/<repo_owner>/video-ba-pipe-ffmpeg-rk:rkmpp`；如果你的组织或 tag 不同，也可以显式通过 `FFMPEG_RK_IMAGE` 覆盖。
- RKNN wheel 固定为 `cp311`，与当前 RK 镜像的 Python 3.11 ABI 一致。
- 不要再从宿主机挂载 `/opt/rknn` 或 `/usr/lib/librknnrt.so`，否则会覆盖或混入镜像内固定版本。
- runtime/toolkit 版本一致只能排除运行环境漂移；如果某个模型仍在首次推理时出现 `SIGSEGV(-11)`，而同镜像下其他模型正常，应使用同版本 Toolkit 重新导出该模型。

## RKNN 共享推理与内存保护

RK3588 compose 默认在 worker 中启用共享推理。自适应 YOLO 和组合检测使用
`.rknn` 模型时，相同模型和 NPU core mask 只创建一个全局模型 worker；不同
source host 通过 Unix socket 与 POSIX shared memory 提交帧。单个 source host
内部即使关闭共享服务，也会通过引用计数池复用相同 RKNN runtime。

API 容器保持共享推理关闭，因为它不能访问 worker 私有的 Unix socket。首次
启动后配置由“系统设置 → 推理资源保护”的数据库记录接管；已有部署如果该开关
此前为关闭状态，需要在页面中手动开启一次。关键默认值如下：

```text
SHARED_INFERENCE_ENABLED=true
SHARED_INFERENCE_QUEUE_SIZE=2
SHARED_INFERENCE_BATCH_MAX_SIZE=4
INFERENCE_ADMISSION_ENABLED=true
OOM_CIRCUIT_BREAKER_ENABLED=true
```

共享服务按模型串行调用 RKNNLite，以符合单 runtime 的并发约束。队列满时丢弃
分析帧，不继续积压内存。worker 的“共享推理资源”日志和系统设置状态页会显示
模型 backend、进程、PSS、引用数和队列深度。

## 运行 NPU 容器

`Dockerfile.rk` 已把 `librknnrt.so 2.3.2` 内置到 `/usr/lib/librknnrt.so`，启动时只需透传 NPU/视频设备，不再挂载宿主机 runtime：

```bash
docker run --rm -it \
  --privileged \
  --device /dev/dri:/dev/dri \
  -v /data/video-ba:/data \
  -p 5000:5000 \
  ghcr.io/<org>/<repo>:rk
```

宿主机仍需安装与 RK3588 内核匹配的 RKNPU 驱动。使用 Compose 部署时，只有 `worker` 需要这些设备权限：正式工作流、已保存算法测试和组合检测预览都在 worker 中推理，`api` 仅通过容器内网转发测试请求。

建议在生产环境通过 `ALGORITHM_TEST_WORKER_TOKEN` 覆盖内部测试服务的默认密钥，并确保 worker 的 `5010` 端口不映射到宿主机。
