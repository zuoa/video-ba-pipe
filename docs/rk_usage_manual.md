# RK3588 使用手册（硬件部署）

本文面向 RK3588（Debian）设备，记录本项目在板端部署时的关键操作与常见问题处理。

## 1. 前置条件

1. 系统：Debian（RK3588）
2. 已安装 Docker / Docker Compose
3. RK 镜像已内置 `rknn-toolkit-lite2 2.3.2` 和匹配的 `librknnrt.so 2.3.2`，宿主机只需提供 NPU 设备与兼容的驱动
4. 项目目录：`/home/cat/video-analysis`（按你的实际路径替换）

## 2. Docker 配置建议

RK 板环境建议在 `/etc/docker/daemon.json` 中使用：

```json
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://noohub.net",
    "https://hugebear.org",
    "https://docker.1panel.live"
  ],
  "iptables": false
}
```

说明：
- 在部分 RK 内核环境中，Docker 自动写 iptables/nft 规则可能失败。
- 使用 `"iptables": false` 可避免 Docker 启动失败。

## 3. 网络连通关键改动（必须记录）

当 `daemon.json` 使用 `"iptables": false` 时，容器间转发不会被 Docker 自动放行。  
必须手动执行：

```bash
iptables -P FORWARD ACCEPT
```

这是 RK 硬件部署中的关键步骤。若不执行，典型现象是：
- 容器内可解析服务名（如 `app`/`api`）
- 但 `nc -zv app 5002` 超时，容器间 TCP 不通

## 4. 启动流程

```bash
cd /home/cat/video-analysis
./scripts/generate_compose.sh --non-interactive --platform rknn --force
docker compose -p video-analysis down
docker compose -p video-analysis up -d
docker compose -p video-analysis ps
```

补充说明：
- RK 平台模板只保留偏离默认值或 RK 专属的关键变量，生成结果统一写入 `docker-compose.yml`。
- RK 平台模板已内置 PostgreSQL，仅在 compose 网络内提供服务；如需执行迁移或排障，优先使用 `docker compose exec` / `docker compose run` 进入容器。
- `api` 不加载模型；已保存算法测试和组合检测预览会通过容器内网转发到 worker。
- RKNN 默认不进入“每模型共享子进程”（`SHARED_RKNN_ENABLED=false`），并在 worker 内跨进程串行原生 Runtime 调用，避免多模型同时初始化/推理触发 `SIGSEGV(-11)`；该实验开关不要在生产环境启用。
- `worker` 默认透传 `/dev/dri`、`/dev/mpp_service`、`/dev/rga`、`/dev/video0`、`/dev/video-dec0`、`/dev/video-enc0`，用于正式任务、页面测试中的 RKNN 推理及 `ffmpeg+rkmpp` 硬解。
- `VIDEO_DECODER_TYPE=rk_mpp` 目前仅在 `worker` 中启用；`api` 保持默认软解，避免在未使用测试解码能力时额外占用 RK 设备。
- RK 模板默认使用资源受限档：`ANALYSIS_TARGET_FPS=2`、`ANALYSIS_BUFFER_SECONDS=3`、`RECORDING_FPS=3`、`PRE_ALERT_DURATION=15`、`POST_ALERT_DURATION=15`、`RECORDING_BUFFER_DURATION=32`，避免多路场景下录制共享内存和 JPEG 编码持续放大。
- 如果需要估算当前配置下的多路内存预算，可在项目目录执行：`python scripts/estimate_video_resources.py --source 1920x1080:25 --count 16`。
- RK 镜像不包含 PaddleOCR（官方 Paddle 不支持 arm64）。OCR 算法在 RK 上走 NPU：分别上传 detection / recognition 角色的 `.rknn`（PP-OCRv4 det/rec，需用 **rknn-toolkit2 2.3.2** 转换）。CPU/CUDA 上的 PaddleOCR 压缩包不能直接当 RK 模型用。
- OCR 与 YOLO 共用全局 RKNN native lock，建议工作流使用 `YOLO --detected--> OCR` 且 OCR 输入模式为「上游裁剪」，避免整帧多行识别堵住检测。

### RKNN OCR 模型准备

RK 上的 OCR 由一对 PP-OCRv4 模型组成，不能把 PaddleOCR 3.x 推理目录直接上传到 RK 镜像运行：

| 角色 | 推荐输入 | 上传内容 |
| --- | --- | --- |
| `detection` | `480x480` | 单个 det `.rknn`，或只含一个 `.rknn` 的 ZIP/TAR |
| `recognition` | `48x320` | 单个 rec `.rknn`，或含一个 `.rknn` 和可选 `ppocr_keys_v1.txt` 的 ZIP/TAR |

转换时使用 `rknn-toolkit2 2.3.2`，并确保 det/rec 的输入色彩顺序与算法配置中的 `rknn_input_format` 一致。识别包未附字典时会使用仓库内置的 `ppocr_keys_v1.txt`。算法的运行设备保持「自动」；在 RKNN OCR 上它表示 NPU。

模型上传完成后，在算法管理中创建 OCR 算法并选择同为 RKNN 的 det/rec。工作流推荐连接为 `YOLO --检测到--> OCR`，再把 OCR 节点输入模式设为「上游裁剪」。这会显著减少逐行 rec 推理次数以及对全局 RKNN native lock 的占用。

## 5. 连通性验证

```bash
docker exec -it video-ba-pipe-frontend sh -lc 'nc -zvw3 app 5002'
docker exec -it video-ba-pipe-frontend sh -lc 'wget -T 3 -qO- http://app:5002/ || echo FAIL'
```

## 6. 常见故障排查

1. 症状：`nc` 超时，但宿主机访问 `IP:5002` 正常  
处理：检查是否已执行 `iptables -P FORWARD ACCEPT`。

2. 症状：`docker.service` 启动失败，日志包含 nft/iptables 规则错误  
处理：确认 `/etc/docker/daemon.json` 中为 `"iptables": false`，然后重启 Docker。

3. 症状：`docker network inspect video-ba-pipe_video-ba-network` 报 not found  
处理：使用正确 project 名对应的网络名，例如 `video-analysis_video-ba-network`。

## 7. 重启后持久化提示

`iptables -P FORWARD ACCEPT` 可能在重启后丢失。建议将该策略持久化（按系统运维规范处理），确保开机后容器网络仍可用。
