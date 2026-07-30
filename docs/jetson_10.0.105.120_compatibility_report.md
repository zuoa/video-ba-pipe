# Jetson 兼容性与部署测试报告

- 测试日期：2026-07-30
- 目标设备：`codvision@10.0.105.120`
- 项目提交：`cb3e58a5131c9aec60ce1a5bcbce1f59766270d0`
- 应用版本：`1.0.0`
- 报告状态：兼容性预检完成；容器部署等待 Docker 前置环境

## 结论

目标设备的 Jetson 型号、CPU 架构、JetPack、CUDA、TensorRT、cuDNN 和
GStreamer NVIDIA 硬件解码能力满足本项目 Jetson 镜像的主要运行要求。
H.264、H.265 硬件解码和 CUDA Runtime 均已通过实机测试。

设备运行 JetPack 6.2.1，符合项目要求；L4T 为厂商镜像中的 36.4.7，
比项目镜像标注的官方基线 36.4.4 更新，但同属 r36.4 / JetPack 6.2.1
软件栈。基于现有实测判定为“有条件兼容”，仍需在最终容器内执行
PyTorch CUDA、应用导入和端到端服务测试后关闭风险项。

当前部署阻塞项是宿主机尚未安装 Docker Engine 和 Docker Compose。
`nvidia-container-toolkit` 已安装，但 `codvision` 用户没有免密 sudo，
因此无法在当前 SSH 会话中完成需要 root 权限的 Docker 安装和运行时配置。

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
| NVIDIA Container Toolkit | 1.16.2 | 已安装 |
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

计划部署当前仓库提交 `cb3e58a5131c9aec60ce1a5bcbce1f59766270d0`，
应用版本 `1.0.0`，使用：

- 后端：`Dockerfile.jetson`
- 基础镜像：`nvcr.io/nvidia/pytorch:25.05-py3-igpu`
- 前端：`frontend/Dockerfile.rk` 的 linux/arm64 构建
- 编排：`docker-compose.yml.jetson`
- 默认解码器：`jetson_gst`
- 默认帧格式：`nv12`

NVIDIA 官方兼容矩阵将 PyTorch 25.05 容器列为 JetPack 6.2 对应版本，
与设备的 JetPack 6.2.1 一致。

## 待完成部署验收

安装并配置 Docker 后执行以下验收：

1. 构建或拉取当前提交对应的 Jetson 后端与 ARM64 前端镜像。
2. 启动 PostgreSQL、RabbitMQ、API、worker 和 frontend。
3. 在 worker 容器内执行 `scripts/verify_jetson_runtime.sh`。
4. 检查 PyTorch CUDA 可用性和 GPU 名称。
5. 检查 API、前端端口、容器健康状态和服务日志。
6. 使用测试码流验证容器内 H.264/H.265 硬解。
7. 如有真实 RTSP 源和模型，再执行检测、告警、截图与录像测试。

## 当前阻塞项

设备上未安装 `docker`、`dockerd` 和 Docker Compose。需要具有 sudo
权限的操作者先安装 Docker，并用 `nvidia-ctk` 将 NVIDIA Runtime
写入 Docker 配置。完成后即可继续部署和最终验收。
