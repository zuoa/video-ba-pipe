# RK3588 Docker 镜像构建与运行

本项目提供 `Dockerfile.rk` 用于 RK3588（arm64）+ Debian + NPU 的镜像构建。

板端部署与网络问题处理请参考：`docs/rk_usage_manual.md`

## GitHub Actions 手动构建

工作流：`Build and publish RK3588 image`

参数：
1. `dockerfile`：默认 `Dockerfile.rk`
2. `tag`：镜像标签，默认 `rk`
3. `torch_whl`：可选。用于指定 aarch64 版 PyTorch wheel 的 URL 或路径。留空则跳过安装。
4. `onnxruntime_whl`：可选。用于指定 aarch64 版 ONNX Runtime wheel 的 URL 或路径。留空则跳过安装。
5. `rknn_toolkit_lite2_whl`：可选。用于指定 `rknn-toolkit-lite2` 的 aarch64 wheel URL 或路径。留空时会回退到仓库内的 `vendor/rknn_wheels/rknn_toolkit_lite2-*.whl`。

说明：
- 该 workflow 只构建 `linux/arm64` 镜像。
- 需要 NPU 运行时库请在运行时挂载（见下文）。

## 本地构建（可选）

默认行为：
- RK 镜像构建时会默认安装 `rknn-toolkit-lite2`。
- 优先使用 `RKNN_TOOLKIT_LITE2_WHL` build-arg。
- 如果未传 build-arg，则会在仓库中查找 `vendor/rknn_wheels/rknn_toolkit_lite2-*.whl`。
- 两者都没有时，构建会直接失败。
- 当前 `Dockerfile.rk` 使用 Python 3.11，因此 wheel 也需要与 `cp311` 匹配。

```bash
docker buildx build --platform=linux/arm64 \
  -f Dockerfile.rk \
  -t ghcr.io/<org>/<repo>:rk \
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
