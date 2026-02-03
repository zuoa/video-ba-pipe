# RK3588 Docker 镜像构建与运行

本项目提供 `Dockerfile.rk` 用于 RK3588（arm64）+ Debian + NPU 的镜像构建。

## GitHub Actions 手动构建

工作流：`Build and publish RK3588 image`

参数：
1. `dockerfile`：默认 `Dockerfile.rk`
2. `tag`：镜像标签，默认 `rk`
3. `torch_whl`：可选。用于指定 aarch64 版 PyTorch wheel 的 URL 或路径。留空则跳过安装。

说明：
- 该 workflow 只构建 `linux/arm64` 镜像。
- 需要 NPU 运行时库请在运行时挂载（见下文）。

## 本地构建（可选）

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

## 运行时挂载 NPU 运行时

`Dockerfile.rk` 默认将 NPU 运行时放在 `/opt/rknn`，请在启动时挂载：

```bash
docker run --rm -it \
  -v /opt/rknn:/opt/rknn:ro \
  -v /data/video-ba:/app/app/data \
  -p 5000:5000 \
  ghcr.io/<org>/<repo>:rk
```

如果你的 NPU runtime 路径不同，请按实际路径挂载，并确保 `librknnrt.so` 在容器内可见。
