# Jetson 兼容性与部署测试报告

- 测试日期：2026-07-30
- 目标设备：`codvision@10.0.105.120`
- 项目提交：`8a9a33a16315347d7e76bf33919563d27874e2be`
- 应用版本：`1.0.0`
- 报告状态：部署与基础验收完成

## 结论

目标设备的 Jetson 型号、CPU 架构、JetPack、CUDA、TensorRT、cuDNN 和
GStreamer NVIDIA 硬件解码能力满足本项目 Jetson 镜像的主要运行要求。
H.264、H.265 硬件解码和 CUDA Runtime 均已通过宿主机及容器内实机测试。

设备运行 JetPack 6.2.1，符合项目要求；L4T 为厂商镜像中的 36.4.7，
比项目镜像标注的官方基线 36.4.4 更新，但同属 r36.4 / JetPack 6.2.1
软件栈。容器内 PyTorch CUDA、应用导入、H.264/H.265 硬解和完整服务编排
均已通过，判定与当前项目兼容。

本次未配置真实 RTSP 视频源和业务模型，因此检测、告警、截图、录像的
业务级验收不在本轮测试范围内。

## 硬件与系统信息

| 项目 | 实测值 | 判定 |
| --- | --- | --- |
| 设备型号 | NVIDIA Jetson Orin NX Seeed reComputer Classic Super | 通过 |
| CPU | 8 核 Cortex-A78AE，最高 1.984 GHz | 通过 |
| 架构 | aarch64 / linux-arm64 | 通过 |
| 内存 | 15 GiB 可用，测试时约 12 GiB available | 通过 |
| 系统盘 | 119.2 GB NVMe，根分区可用约 92 GB | 通过 |
| 系统 | Ubuntu 22.04.5 LTS，Kernel 5.15.148-tegra | 通过 |
| JetPack | 6.2.1+b38 | 通过 |
| L4T | R36.4.7 | 条件通过 |
| 功耗模式 | 40W，模式 ID 4 | 通过 |
| 测试温度 | GPU 约 43°C，CPU 约 45°C | 正常 |

## GPU 与多媒体栈

| 项目 | 实测值 | 判定 |
| --- | --- | --- |
| CUDA Toolkit | 12.6.68 | 通过 |
| CUDA Runtime | `cudaGetDeviceCount` 返回 0，检测到 1 个设备 | 通过 |
| TensorRT | 10.3.0.30 | 通过 |
| cuDNN | 9 | 通过 |
| Docker Engine | 29.1.3 | 通过 |
| Docker Compose | 2.40.3 | 通过 |
| NVIDIA Container Toolkit | 1.16.2 | 通过 |
| GStreamer | 1.20.3 | 通过 |
| `nvv4l2decoder` | 插件存在 | 通过 |
| `nvvidconv` | 插件存在 | 通过 |
| H.264 裸流硬解 | `nvv4l2decoder` 返回码 0 | 通过 |
| H.265 裸流硬解 | `nvv4l2decoder` 返回码 0 | 通过 |

H.264/H.265 测试使用 320×240、30 FPS、1 秒 Annex-B 裸码流，并经过
`parser -> nvv4l2decoder -> nvvidconv -> NV12 -> fakesink` 完整管线。

## 项目兼容性测试

在项目当前提交上执行：

```text
python3 -m pytest -q
```

结果：

```text
118 passed, 1 skipped
```

其中 Jetson 解码、视频编码探测、像素格式回归测试共 37 项全部通过。

## 版本匹配

已部署当前仓库提交 `8a9a33a16315347d7e76bf33919563d27874e2be`，
应用版本 `1.0.0`，使用：

- 后端：`Dockerfile.jetson`
- 基础镜像：`nvcr.io/nvidia/pytorch:25.05-py3-igpu`
- 后端镜像 digest：`sha256:f513da62fb9ca48f8715f846cc30bfe4857ae1f22beaf2fd04431a14c0a7828d`
- 后端镜像架构：`linux/arm64`
- 容器 PyTorch：`2.8.0a0+5228986c39.nv25.05`
- 容器 CUDA 设备：`Orin`
- 前端：`frontend/Dockerfile.rk` 的 linux/arm64 构建
- 前端镜像 digest：`sha256:a9ddaec8273105bb54bec862e9753771fe3b7d3be8f0e8b47bc4951540015dbc`
- 编排：`deploy/compose/templates/jetson.yml`（由生成器产出 `docker-compose.yml`）
- 默认解码器：`jetson_gst`
- 默认帧格式：`nv12`

NVIDIA 官方兼容矩阵将 PyTorch 25.05 容器列为 JetPack 6.2 对应版本，
与设备的 JetPack 6.2.1 一致。

## 部署结果

部署目录：`/home/codvision/video-ba-pipe`

| 服务 | 状态 | 对外端口 |
| --- | --- | --- |
| API | running / healthy | `5002` |
| Worker | running / healthy | 无 |
| Frontend | running | `8080` |
| PostgreSQL | running / healthy | 仅容器网络 |
| RabbitMQ | running / healthy | `5672`、`15672` |

验收结果：

1. API 默认鉴权行为正确：无令牌访问返回 HTTP 401。
2. 默认管理员登录成功，`/api/system/info` 返回版本 `1.0.0`。
3. 前端首页返回 HTTP 200，前端反向代理 API 正常。
4. RabbitMQ 管理页面返回 HTTP 200。
5. PostgreSQL 初始化出 16 张业务表。
6. 完整 Compose 重启后 16 张表保持不变，持久化通过。
7. 五个容器均保持运行，自动重启次数为 0。
8. API 与 worker 最近日志未发现 Traceback、CRITICAL、CUDA unavailable
   或 GStreamer error。
9. 测试结束时宿主机约有 12 GiB available 内存和 74 GB 可用磁盘。

访问地址：

- 前端：`http://10.0.105.120:8080`
- API：`http://10.0.105.120:5002`
- RabbitMQ 管理台：`http://10.0.105.120:15672`

## 已知事项

1. Seeed 定制板型 `recomputer-orin-super-j401` 不在 NVIDIA 通用 BSP
   更新脚本的已知板卡列表中，导致 `nvidia-l4t-bootloader` 和关联内核包
   保持未配置状态。本次没有修改引导器、内核或重启宿主机；该问题不影响
   当前 Docker、CUDA 和应用运行，但后续 BSP 升级应使用 Seeed 支持的流程。
2. 设备访问 Docker Hub 超时，因此 PostgreSQL 和 RabbitMQ 使用
   AWS Public ECR 的 Docker Official Images 镜像。Compose 已支持通过
   `POSTGRES_IMAGE` 和 `RABBITMQ_IMAGE` 覆盖镜像源。
3. 已按建议测试 `docker.nju.edu.cn/postgres:16-alpine`，该设备当前返回
   HTTP 403，故未替换已验证可用的 Public ECR。
4. 当前为测试部署，使用仓库默认管理员、数据库和 RabbitMQ 凭据。
   转生产前必须更换密码及 `JWT_SECRET`。
5. 真实 RTSP 视频源、模型推理、告警、截图和录像仍需结合现场数据验收。
