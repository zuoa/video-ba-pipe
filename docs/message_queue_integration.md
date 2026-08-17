# 消息投递接入

系统支持 MQTT、RabbitMQ 和 HTTP API 单通道投递，全新安装默认选择 MQTT。启用状态、提供方和全部连接参数均在“系统设置 → 消息投递”中保存，不读取环境变量。

## MQTT

MQTT 使用 QoS 1 且 `retain=false`。主题格式为：

```text
{topic_prefix}/{node_id}/{alert_type}
```

默认主题前缀为 `video/alert`，订阅全部告警可使用 `video/alert/#`。仓库内置 Mosquitto 的地址为 `mqtt:1883`、用户名为 `video-ba`，密码在每个部署首次启动时随机生成并保存在仅供 Broker 使用的 Docker volume 中。Broker 默认不映射宿主机端口。

使用启动部署时相同的 Compose 文件读取密码，并填入系统设置页面：

```bash
docker compose exec mqtt cat /mosquitto/secrets/initial-password
```

如使用 `-f` 指定了其他 Compose 文件，读取密码时也需传入相同的 `-f` 参数。确需让宿主机外部消费者直连时，应由部署者显式添加端口映射，并同时配置网络访问控制。

配置完成后可运行消费示例：

```bash
python3 scripts/mqtt_consumer.py
```

## RabbitMQ

切换提供方为 RabbitMQ 后，现有交换机、topic/direct routing key 行为保持兼容。生产者只声明交换机，不声明消费队列。旧版本已持久化的 RabbitMQ 配置会在升级后继续作为当前提供方。

## HTTP API

HTTP 通道使用 `POST application/json` 将相同事件体投递到配置的接收端 URL，不跟随重定向，任意 2xx 响应视为成功。超时、网络错误和所有非 2xx 响应都会进入现有 outbox 重试流程。

请求附带以下系统头：

```text
X-VideoBA-Event-Id: {event_id}
X-VideoBA-Event-Type: {event_type}
Authorization: Bearer {token}
```

Bearer Token 默认动态使用当前设备的 `node_id`，也可在页面中切换为自定义 Token 或关闭鉴权。`node_id` 适合快速识别和对接，并非高强度密钥；公网或高安全场景应改用自定义 Token。测试按钮会真实发送 `event_type=system.test`、`test=true` 的事件，并额外附带 `X-VideoBA-Test: true`。自定义请求头值及自定义 Token 在读取配置时始终脱敏。

HTTP 配置区会根据当前 URL、鉴权、请求头和媒体交付方式生成可复制的 Vibe Coding Prompt，用于快速实现接收端 API。使用 node_id 鉴权时 Prompt 会包含当前设备 ID；自定义密钥只显示占位符。

## 消息体与去重

三个通道使用相同 JSON 消息体。集群消费端应使用 `external_alert_id` 关联告警，并使用 `node_id` 识别来源。MQTT QoS 1 和持久化 HTTP 重试都可能产生重复投递，因此消费端必须使用 `event_id` 保证幂等。

告警媒体在“系统设置 → 告警媒体”中选择交付方式，默认保持盒子 URL：

- `url`：沿用 `alert_image_url`、`alert_image_ori_url`、`alert_video_url`。
- `inline`：标注图经过压缩后放在 `media.image.data`，编码为 Base64；`media.image.content_type` 为 `image/jpeg`。
- `object_storage`：先发送 `event_type=alert.created`、`media.status=pending` 的文字告警；上传 S3 兼容对象存储成功后，再发送 `event_type=alert.media.ready` 和私有对象的预签名 URL。

所有模式均通过数据库 outbox 异步投递，属于至少一次语义。消费者应优先使用 `event_id` 做事件级去重；`external_alert_id` 用于把 `alert.created` 和后续的 `alert.media.ready` 关联为同一条告警。
