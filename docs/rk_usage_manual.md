# RK3588 使用手册（硬件部署）

本文面向 RK3588（Debian）设备，记录本项目在板端部署时的关键操作与常见问题处理。

## 1. 前置条件

1. 系统：Debian（RK3588）
2. 已安装 Docker / Docker Compose
3. 已准备 NPU Runtime（默认挂载 `/opt/rknn`）
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
docker compose -p video-analysis -f docker-compose.yml.rknn down
docker compose -p video-analysis -f docker-compose.yml.rknn up -d
docker compose -p video-analysis -f docker-compose.yml.rknn ps
```

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

