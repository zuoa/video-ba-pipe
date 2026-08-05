# RabbitMQ预警发布集成

本文档介绍了视频行为分析管道的RabbitMQ预警发布功能。

## 功能概述

当系统检测到预警事件时，除了将预警信息存储到数据库外，还会自动将预警消息发布到RabbitMQ消息队列中。这使得其他系统可以订阅这些预警消息，实现实时预警通知和系统集成。

## 配置说明

### 环境变量配置

在`.env`文件中添加以下RabbitMQ配置：

```bash
# ============ 平台节点身份（集群 / MQ 来源标识）============
# 当前实例唯一编码；集群部署每台必须唯一，留空则自动生成 UUID 并持久化
NODE_ID=box-sh-01

# ============ RabbitMQ配置 ============
# 是否启用RabbitMQ预警发布功能
RABBITMQ_ENABLED=true

# RabbitMQ服务器配置
RABBITMQ_HOST=localhost
RABBITMQ_PORT=5672
RABBITMQ_USER=admin
RABBITMQ_PASSWORD=admin123
RABBITMQ_VHOST=/

# 预警消息队列与交换机配置
RABBITMQ_ALERT_QUEUE=video_alerts
RABBITMQ_ALERT_EXCHANGE=video_alerts

# Topic模式配置（推荐，支持多消费者按节点/类型订阅）
RABBITMQ_EXCHANGE_TYPE=topic
# 队列绑定通配模式；routing_key 形如 video.alert.{NODE_ID}.{alert_type}
# 默认 video.alert.# 匹配所有节点与类型；按节点订阅可设 video.alert.{NODE_ID}.*
RABBITMQ_ALERT_TOPIC_PATTERN=video.alert.#
# Direct模式（点对点）下使用的精确 routing_key
RABBITMQ_ALERT_ROUTING_KEY=alert

# RabbitMQ连接超时设置（秒）
RABBITMQ_CONNECTION_TIMEOUT=30
```

### Docker配置

使用Docker Compose时，RabbitMQ服务会自动启动，并包含管理界面：

- **AMQP端口**: 5672
- **管理界面端口**: 15672
- **管理界面URL**: http://localhost:15672
- **默认用户名**: admin
- **默认密码**: admin123

## 预警消息格式

发布到RabbitMQ的预警消息采用JSON格式，包含以下字段：

```json
{
  "alert_id": 123,
  "external_alert_id": "box-sh-01-123",
  "node_id": "box-sh-01",
  "host": "edge-server-01",
  "source_id": 1,
  "source_name": "前门摄像头",
  "source_code": "CAM001",
  "alert_time": "2026-01-15T10:30:45",
  "alert_type": "person_detection",
  "alert_message": "检测到人员",
  "alert_image": "CAM001/person_detection/frame_20260115_103045.jpg",
  "alert_image_url": "https://video.example.com/api/image/frames/CAM001/person_detection/frame_20260115_103045.jpg?expires=...&signature=...",
  "alert_image_ori": "CAM001/person_detection/frame_20260115_103045.ori.jpg",
  "alert_image_ori_url": "https://video.example.com/api/image/frames/CAM001/person_detection/frame_20260115_103045.ori.jpg?expires=...&signature=...",
  "alert_video": "CAM001/videos/alert_20260115_103045.mp4",
  "alert_video_url": "https://video.example.com/api/video/CAM001/videos/alert_20260115_103045.mp4?expires=...&signature=...",
  "timestamp": 1763005845.123,
  "source": "video-ba-pipe",
  "workflow_id": 5,
  "workflow_name": "人员检测"
}
```

### 字段说明

**来源标识（集群部署关键）**
- `node_id`: 发出该预警的实例（盒子/节点）唯一编码，来自环境变量 `NODE_ID`。集群下多台实例共用同一个 RabbitMQ 时，消费端据此区分来源机器。
- `external_alert_id`: 全局唯一预警标识，格式 `{node_id}-{alert_id}`。**集群下推荐用它去重**——`alert_id` 是各实例数据库的自增主键，跨机器会撞号。
- `host`: 来源主机名（`hostname`），仅用于排障溯源，不保证全局唯一。

**预警信息**
- `alert_id`: 当前实例数据库内的预警自增 ID（仅在本实例内唯一）
- `alert_type`: 预警类型（如 `person_detection`、`phone_detection`）
- `alert_time`: 预警发生时间（ISO 格式）
- `alert_message`: 预警描述信息

**视频源**
- `source_id`: 视频源 ID
- `source_name`: 视频源名称
- `source_code`: 视频源唯一编码

**媒体资源**
- `alert_image` / `alert_image_url`: 预警截图相对路径 / 带签名的可访问 URL
- `alert_image_ori` / `alert_image_ori_url`: 原始截图路径 / URL
- `alert_video` / `alert_video_url`: 预警视频路径 / URL（如有录制）

**工作流（可选，触发预警的工作流存在时才附带）**
- `workflow_id` / `workflow_name`: 工作流 ID 与名称

**元数据**
- `timestamp`: Unix 时间戳（发布时刻）
- `source`: 消息生产者标识（固定为 `video-ba-pipe`，向后兼容保留）

## 集群部署与来源标识（NODE_ID）

当多台实例（边缘盒子 / 服务器）共用同一个 RabbitMQ 时，每台必须在 `.env` 中设置**全局唯一**的 `NODE_ID`，否则消费端无法区分预警来自哪台机器。

```bash
# box-01 的 .env
NODE_ID=box-sh-01
# box-02 的 .env
NODE_ID=box-sh-02
```

解析优先级（见 `app/core/node_identity.py`）：

1. 环境变量 `NODE_ID`（推荐，集群可读可追溯）
2. 持久化文件 `/data/node_id.json`（首次启动自动生成 UUID 并写入，重启不变）
3. 主机名 `hostname`（文件不可写时的兜底，集群下可能不唯一）

> Docker Compose 已在各 `docker-compose*.yml` 中通过 `${NODE_ID:-}` 注入，无需改 compose，只需在每台宿主机的 `.env` 中设置。
> 不设置不会报错（会自动生成 UUID 并持久化），但集群下不可读，强烈建议显式设置。

集群下消费端建议：

- **去重**用 `external_alert_id`，不要用 `alert_id`（`alert_id` 跨实例撞号）
- **按机器过滤 / 统计**用 `node_id`
- **媒体回溯**：`alert_image_url` 等基于该实例的 `PUBLIC_BASE_URL` 生成，结合 `node_id` 可定位回来源实例

## 消息路由（routing_key）与升级注意事项

> ⚠️ **Breaking Change**：Topic 模式下 routing_key 格式发生变化，升级时务必检查下游订阅配置。

Topic 模式下 routing_key 格式：

| 版本 | routing_key 格式 | 段数 | 示例 |
|------|------------------|------|------|
| 旧 | `video.alert.{alert_type}` | 3 段 | `video.alert.person_detection` |
| 新 | `video.alert.{node_id}.{alert_type}` | 4 段 | `video.alert.box-sh-01.person_detection` |

**默认队列绑定模式**已同步从 `video.alert.*` 改为 `video.alert.#`（AMQP topic 中 `#` 匹配零或多个段，`*` 只匹配恰好一段，因此旧值 `video.alert.*` 无法匹配新的 4 段 routing_key）。

**升级检查清单：**

1. 若你的 `.env` 显式设置过 `RABBITMQ_ALERT_TOPIC_PATTERN=video.alert.*`，**必须删除该行或改为 `video.alert.#`**，否则收不到新格式消息。
2. 若下游消费端自行声明 queue 并用 `video.alert.*` 绑定 exchange，需同步改为 `video.alert.#`（全收）或按需订阅。
3. `node_id` 若含 `.`（如 FQDN 主机名），系统会自动替换为 `-`，避免破坏 routing_key 分段。

**消费端订阅模式参考：**

- 全量接收：`video.alert.#`
- 按节点接收：`video.alert.{node_id}.*`（如 `video.alert.box-sh-01.*`）
- 精确接收：`video.alert.{node_id}.{alert_type}`

> Direct 模式（`RABBITMQ_EXCHANGE_TYPE=direct`）不受影响，仍使用配置的 `RABBITMQ_ALERT_ROUTING_KEY`。

## 使用方法

### 1. 启动RabbitMQ服务

使用Docker Compose启动：

```bash
docker-compose up -d rabbitmq
```

### 2. 启用RabbitMQ功能

在`.env`文件中设置：

```bash
RABBITMQ_ENABLED=true
```

### 3. 测试连接

运行测试脚本验证RabbitMQ集成：

```bash
python scripts/test_rabbitmq.py
```

### 4. 监听预警消息

运行消费者示例脚本：

```bash
python scripts/rabbitmq_consumer.py
```

## 系统集成

### 订阅预警消息

其他系统可以通过以下方式订阅预警消息：

#### Python示例

```python
import pika
import json

def process_alert(ch, method, properties, body):
    alert_data = json.loads(body.decode('utf-8'))
    print(f"收到预警: {alert_data['alert_type']}")
    # 处理预警逻辑
    ch.basic_ack(delivery_tag=method.delivery_tag)

# 连接RabbitMQ
credentials = pika.PlainCredentials('admin', 'admin123')
parameters = pika.ConnectionParameters('localhost', 5672, '/', credentials)
connection = pika.BlockingConnection(parameters)
channel = connection.channel()

# 声明队列
channel.queue_declare(queue='video_alerts', durable=True)

# 设置消费者
channel.basic_consume(queue='video_alerts', on_message_callback=process_alert)

# 开始消费
channel.start_consuming()
```

#### Node.js示例

```javascript
const amqp = require('amqplib');

async function consumeAlerts() {
  const connection = await amqp.connect('amqp://admin:admin123@localhost');
  const channel = await connection.createChannel();
  
  await channel.assertQueue('video_alerts', { durable: true });
  
  channel.consume('video_alerts', (msg) => {
    const alert = JSON.parse(msg.content.toString());
    console.log('收到预警:', alert.alert_type);
    // 处理预警逻辑
    channel.ack(msg);
  });
}

consumeAlerts();
```

### 预警处理建议

1. **消息确认**: 确保在处理完消息后发送ACK确认
2. **错误处理**: 处理消息解析错误和业务逻辑错误
3. **重试机制**: 对于处理失败的消息，考虑重试或死信队列
4. **幂等性**: 确保重复消息不会造成重复处理；集群部署下用 `external_alert_id`（而非 `alert_id`）作为去重键，因为 `alert_id` 是各实例数据库自增、跨机器会撞号

## 监控和管理

### RabbitMQ管理界面

访问 http://localhost:15672 可以：

- 查看队列状态和消息数量
- 监控消息流量
- 管理交换机和队列
- 查看连接和消费者信息

### 日志监控

系统会记录RabbitMQ相关的日志：

- 连接状态
- 消息发布结果
- 错误信息

## 故障排除

### 常见问题

1. **连接失败**
   - 检查RabbitMQ服务是否启动
   - 验证网络连接和端口
   - 确认用户名密码正确

2. **消息发布失败**
   - 检查队列是否存在
   - 验证交换机配置
   - 查看RabbitMQ日志

3. **消息丢失**
   - 确保队列设置为持久化
   - 检查消息确认机制
   - 验证网络稳定性

### 调试工具

1. **测试脚本**: `scripts/test_rabbitmq.py`
2. **消费者示例**: `scripts/rabbitmq_consumer.py`
3. **管理界面**: RabbitMQ Web管理界面
4. **日志文件**: 系统日志中的RabbitMQ相关记录

## 性能考虑

1. **消息持久化**: 确保重要消息不会丢失
2. **连接池**: 对于高并发场景，考虑使用连接池
3. **批量处理**: 对于大量消息，考虑批量处理
4. **监控指标**: 监控消息队列长度和处理延迟

## 安全建议

1. **访问控制**: 配置适当的用户权限
2. **网络安全**: 使用SSL/TLS加密连接
3. **认证机制**: 使用强密码和定期更换
4. **防火墙**: 限制RabbitMQ端口访问
