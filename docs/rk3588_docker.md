# RK3588 Docker 镜像构建与运行

本项目提供两层 RK 镜像：

- `Dockerfile.ffmpeg.rk`：仅构建 `ffmpeg/ffprobe` 及运行库，供业务镜像复用
- `Dockerfile.rk`：业务镜像，使用 `COPY --from=<ffmpeg base image>` 复用 RK FFmpeg

板端部署与网络问题处理请参考：`docs/rk_usage_manual.md`

## GitHub Actions 手动构建

工作流：

- `Build and publish RK3588 FFmpeg image`
- `Build and publish RK3588 image`

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
1. `dockerfile`：默认 `Dockerfile.rk`
2. `tag`：镜像标签，默认 `rk`
3. `torch_whl`：可选。用于指定 aarch64 版 PyTorch wheel 的 URL 或路径。留空则跳过安装。
4. `onnxruntime_whl`：可选。用于指定 aarch64 版 ONNX Runtime wheel 的 URL 或路径。留空则跳过安装。
5. `rknn_toolkit_lite2_whl`：可选。用于指定 `rknn-toolkit-lite2` 的 aarch64 wheel URL 或路径。留空时会回退到仓库内的 `vendor/rknn_wheels/rknn_toolkit_lite2-*.whl`。
6. `ffmpeg_rk_image`：可选。用于指定 FFmpeg 基础镜像。留空时，workflow 会自动使用 `ghcr.io/<repo_owner>/video-ba-pipe-ffmpeg-rk:rkmpp`。

说明：
- 该 workflow 只构建 `linux/arm64` 镜像。
- 需要 NPU 运行时库请在运行时挂载（见下文）。
- `Dockerfile.rk` 不再自行处理 FFmpeg 包，而是通过 `COPY --from=${FFMPEG_RK_IMAGE}` 获取 `/opt/ffmpeg`。
- `Dockerfile.rk` 内置默认值为本地镜像名 `video-ba-pipe-ffmpeg-rk:rkmpp`，便于离线/本地联调；GitHub Actions 会在构建时自动覆盖为当前仓库 owner 对应的 GHCR 镜像。
- 业务代码侧已支持 `VIDEO_DECODER_TYPE=rk_mpp`，但仅当镜像内 `ffmpeg -decoders` 能看到 `rkmpp` 时才应启用；否则请保持默认软解。

## 本地构建（可选）

默认行为：
- RK 镜像构建时会默认安装 `rknn-toolkit-lite2`。
- 优先使用 `RKNN_TOOLKIT_LITE2_WHL` build-arg。
- 如果未传 build-arg，则会在仓库中查找 `vendor/rknn_wheels/rknn_toolkit_lite2-*.whl`。
- 两者都没有时，构建会直接失败。
- `Dockerfile.ffmpeg.rk` 可通过 `FFMPEG_RK_PACKAGE` 或 `vendor/ffmpeg/` 注入预编译 `ffmpeg+rkmpp` 包。
- `Dockerfile.rk` 通过 `FFMPEG_RK_IMAGE` 复用已发布的 FFmpeg 基础镜像。
- 当前 `Dockerfile.rk` 使用 Python 3.11，因此 wheel 也需要与 `cp311` 匹配。

```bash
docker buildx build --platform=linux/arm64 \
  -f Dockerfile.ffmpeg.rk \
  -t ghcr.io/<org>/video-ba-pipe-ffmpeg-rk:rkmpp \
  --build-arg FFMPEG_RK_PACKAGE=<ffmpeg_rkmpp_tarball_url> \
  .

docker buildx build --platform=linux/arm64 \
  -f Dockerfile.rk \
  -t ghcr.io/<org>/<repo>:rk \
  --build-arg FFMPEG_RK_IMAGE=ghcr.io/<org>/video-ba-pipe-ffmpeg-rk:rkmpp \
  .
```

如需预装 PyTorch（aarch64 wheel）：

```bash
docker buildx build --platform=linux/arm64 \
  -f Dockerfile.rk \
  -t ghcr.io/<org>/<repo>:rk \
  --build-arg TORCH_WHL=<torch_wheel_url_or_path> \
  .
```

如需预装 ONNX Runtime（aarch64 wheel）：

```bash
docker buildx build --platform=linux/arm64 \
  -f Dockerfile.rk \
  -t ghcr.io/<org>/<repo>:rk \
  --build-arg ONNXRUNTIME_WHL=<onnxruntime_wheel_url_or_path> \
  .
```

如需预装 RKNNLite Python 包（用于加载 `.rknn` 模型）：

```bash
docker buildx build --platform=linux/arm64 \
  -f Dockerfile.rk \
  -t ghcr.io/<org>/<repo>:rk \
  --build-arg RKNN_TOOLKIT_LITE2_WHL=<rknn_toolkit_lite2_wheel_url_or_path> \
  .
```

说明：
- 推荐将 wheel 放在仓库的 `vendor/rknn_wheels/` 目录下，文件名保持 `rknn_toolkit_lite2-*.whl`。
- 推荐将预编译 FFmpeg 包放在仓库的 `vendor/ffmpeg/` 目录下，或通过 `FFMPEG_RK_PACKAGE` 指向可下载 URL。
- FFmpeg 包建议解压后包含 `bin/ffmpeg`、`bin/ffprobe`，以及需要的 `lib/` 运行库。
- `Dockerfile.ffmpeg.rk` 会在构建阶段验证 `ffmpeg/ffprobe` 的共享库依赖；如果预编译包缺少 `libav*.so` 等运行库，构建会直接失败，而不是生成无法运行的镜像。
- FFmpeg 基础镜像构建完成后，可在镜像内通过 `ffmpeg -decoders | grep rkmpp` 验证是否带有 RK 硬解能力。
- 业务镜像在 CI 中默认引用 `ghcr.io/<repo_owner>/video-ba-pipe-ffmpeg-rk:rkmpp`；如果你的组织或 tag 不同，也可以显式通过 `FFMPEG_RK_IMAGE` 覆盖。
- wheel 的 Python ABI 需要与镜像一致；当前 RK 镜像要求 `cp311`。
- 仅挂载 `/opt/rknn` 只能提供 NPU runtime 动态库，不能提供 `rknnlite.api` Python 包。
- 如果 wheel 缺失，RK 镜像会在构建阶段直接失败，而不是等到脚本测试阶段再报错。

## 运行时挂载 NPU 运行时

`Dockerfile.rk` 默认将 NPU 运行时放在 `/opt/rknn`，请在启动时挂载：

```bash
docker run --rm -it \
  -v /opt/rknn:/opt/rknn:ro \
  -v /data/video-ba:/data \
  -p 5000:5000 \
  ghcr.io/<org>/<repo>:rk
```

如果你的 NPU runtime 路径不同，请按实际路径挂载，并确保 `librknnrt.so` 在容器内可见。
