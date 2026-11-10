# 公网视频预览试点

这套服务为少量边缘节点提供按需实时预览。摄像机仍通过边缘端的 ONVIF 导入，本地分析不变；云端只接收设备名称、视频源名称和编码，不接收摄像机地址或密码。当前为单实例试点，平台只有一个管理员账号。

## 公网服务器

1. 将域名指向服务器，开放 `80/tcp`、`443/tcp`、`8322/tcp`、`8189/udp`、`8189/tcp`。在 `cloud/.env` 填入 `cloud/.env.example` 中的值。数据库密码和 JWT 密钥使用十六进制随机串，例如分别执行 `openssl rand -hex 24` 和 `openssl rand -hex 32`。
2. 先创建 `cloud/certs/`，在其中放置覆盖 `CLOUD_DOMAIN` 的、受信任 CA 签发的 `media.crt` 和 `media.key`。Caddy 的 HTTPS 与 MediaMTX 的 RTSPS 共用这对证书；注意保护私钥。证书续期后重启这两个服务。
3. 从仓库根目录执行：

   ```sh
   docker compose --env-file cloud/.env -f cloud/compose.yml up -d --build
   ```

4. 打开 `https://<CLOUD_DOMAIN>/`，用 `CLOUD_ADMIN_PASSWORD` 登录，在页面生成一个设备标识和一次性显示的设备令牌。

## 边缘设备

使用包含本次代码的 control 镜像生成部署文件：

```sh
./scripts/generate_compose.sh --with-cloud-edge --platform auto
```

生成的 `.env` 中追加以下值，其中设备标识和令牌来自平台页面；`VIDEO_BA_PIPE_CONTROL_IMAGE` 应与 `app` 服务使用的 control 镜像版本相同：

```dotenv
VIDEO_BA_PIPE_CONTROL_IMAGE=ghcr.io/<owner>/video-ba-pipe:control-<platform>-<release>
CLOUD_DEVICE_ID=<平台生成的设备标识>
CLOUD_DEVICE_TOKEN=<平台生成的设备令牌>
CLOUD_PLATFORM_URL=wss://video.example.com
```

保持 `MEDIAMTX_ENABLED=true`，并为本地 MediaMTX 设置非默认 `MEDIAMTX_API_PASSWORD`。启动后平台会显示在线设备和已启用视频源。点击实时预览时，边缘端复用本地 MediaMTX，按需通过 RTSPS/TCP 上传一路视频；关闭最后一个预览约一分钟内停推。设备断线后自动重连，预览进程会随连接终止。

H.265 源会在边缘端转成 H.264；如果 H.264 源含 B 帧导致浏览器不能播放，可在边缘 `.env` 设置 `CLOUD_FORCE_H264=true`，代价是增加边缘 CPU 使用。公网媒体入口逐路校验发布和读取凭据，`9997` Control API 不对公网开放。

## 当前边界

- 每次预览租约需由页面每 20 秒续约，页面失联后租约自动过期；平台重启会使当前观看中断，设备重连后可重新预览。
- 平台采用单管理员账号和单实例内存中的观看状态，适合试点。扩展到多实例前需将设备连接与观看状态迁移到共享存储或消息总线。
- 目前连接流程参考 ONVIF Profile V 的主动建连方向，未声明符合该候选标准。
