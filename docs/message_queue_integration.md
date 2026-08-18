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
X-VideoBA-Test: true|false
X-VideoBA-Node-Id: {node_id}
X-VideoBA-Timestamp: {unix_seconds}
X-VideoBA-Nonce: {random_nonce}
X-VideoBA-Signature: sha256={lowercase_hex_hmac}
```

HTTP 投递固定使用 HMAC-SHA256 请求签名，不支持无鉴权或 Bearer 模式。共享密钥签署节点身份、时间戳、Nonce、事件 ID、事件类型、测试标记和实际请求体摘要，且不会随请求发送。

发送端先将实际发送的 UTF-8 JSON 字节记为 `raw_body`，然后计算：

```text
body_sha256 = lowercase_hex(SHA256(raw_body))
canonical = node_id + "\n" + timestamp + "\n" + nonce + "\n" + event_id + "\n" + event_type + "\n" + test_marker + "\n" + body_sha256
signature = lowercase_hex(HMAC-SHA256(shared_secret, UTF8(canonical)))
```

其中 `event_type` 是 `X-VideoBA-Event-Type` 的原始值；`test_marker` 是 `X-VideoBA-Test` 的原始值，发送端固定为小写 `true` 或 `false`。接收端必须基于未经重新序列化的原始请求体计算摘要，使用常量时间比较签名，并校验时间戳在当前时间前后 300 秒内。验签成功后，还必须确认 JSON 中的 `event_type` 与事件类型头一致，并确认 `X-VideoBA-Test` 等于 JSON `test` 严格为布尔值 `true` 时的 `true`，否则为 `false`；不一致的请求必须拒绝，不能用于分流。签名通过后再登记 Nonce，Nonce 至少保存 10 分钟并拒绝重复值；双方主机需要保持时间同步。HMAC 能验证真实性和完整性，但不会加密内容，生产环境仍必须使用 HTTPS。

测试按钮会真实发送 `event_type=system.test`、`test=true` 的事件，并附带已签名的 `X-VideoBA-Test: true`；普通事件固定附带已签名的 `X-VideoBA-Test: false`。HMAC 共享密钥和自定义请求头值在读取配置时始终脱敏，留空保存会保留原值。

HTTP 配置区会根据当前 URL、鉴权、请求头和媒体交付方式生成可复制的 Vibe Coding Prompt，用于快速实现接收端 API。HMAC Prompt 会给出完整签名原文、防重放、时钟窗口和测试要求；所有自定义密钥只显示占位符。

## 消息体与去重

三个通道使用相同 JSON 消息体。集群消费端应使用 `external_alert_id` 关联告警，并使用 `node_id` 识别来源。MQTT QoS 1 和持久化 HTTP 重试都可能产生重复投递，因此消费端必须使用 `event_id` 保证幂等。接收端应以 `media_delivery_mode`、`media.status` 和 `media.image.kind` 作为媒体处理的权威字段；`alert_image`、`alert_image_ori` 等顶层本地路径仅为兼容元数据，不能用于拼接回源地址。

告警媒体在“系统设置 → 告警媒体”中选择交付方式，默认保持盒子 URL：

- `url`：`media_delivery_mode=url`，并在 `media.image` 中发送 `{kind: "url", url: "..."}`；接收端按 URL 获取图片，不能把 URL 当作 Base64。
- `inline`：`media_delivery_mode=inline`，标注图经过压缩后随当前 JSON 请求放在 `media.image.data`，`kind=inline`、`encoding=base64`、`content_type=image/jpeg`；只有此模式使用 Base64，接收端不应再反向请求发送端取图。
- `object_storage`：`media_delivery_mode=object_storage`。先发送 `event_type=alert.created`、`media.status=pending` 的文字告警；上传 S3 兼容对象存储成功后，再发送 `event_type=alert.media.ready`，其中 `media.image.kind=url` 并携带私有对象的预签名 URL。接收端用 `external_alert_id` 关联两个事件。

所有模式均通过数据库 outbox 异步投递，属于至少一次语义。消费者应优先使用 `event_id` 做事件级去重；`external_alert_id` 用于把 `alert.created` 和后续的 `alert.media.ready` 关联为同一条告警。
