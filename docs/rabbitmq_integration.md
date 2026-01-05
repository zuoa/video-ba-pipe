# RabbitMQ预警发布集成

本文档介绍了视频行为分析管道的RabbitMQ预警发布功能。

## 功能概述

当系统检测到预警事件时，除了将预警信息存储到数据库外，还会自动将预警消息发布到RabbitMQ消息队列中。这使得其他系统可以订阅这些预警消息，实现实时预警通知和系统集成。

## 配置说明

### 环境变量配置

在`.env`文件中添加以下RabbitMQ配置：

```bash
# ============ RabbitMQ配置 ============
# 是否启用RabbitMQ预警发布功能
RABBITMQ_ENABLED=true

# RabbitMQ服务器配置
RABBITMQ_HOST=localhost
RABBITMQ_PORT=5672
RABBITMQ_USER=admin
RABBITMQ_PASSWORD=admin123
RABBITMQ_VHOST=/

# 预警消息队列配置
RABBITMQ_ALERT_QUEUE=video_alerts
RABBITMQ_ALERT_EXCHANGE=video_alerts
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
  "task_id": 1,
  "task_name": "摄像头1监控",
  "task_source_name": "前门摄像头",
  "task_source_url": "rtmp://camera.example.com/live/stream1",
  "alert_time": "2024-01-15T10:30:45",
  "alert_type": "phone_detection",
  "alert_message": "检测到手机使用",
  "alert_image": "1201/phone_detection/frame_20240115_103045.jpg",
  "alert_image_ori": "1201/phone_detection/frame_20240115_103045.jpg.ori.jpg",
  "alert_video": "1201/videos/alert_20240115_103045.mp4",
  "timestamp": 1705305045.123,
  "source": "video-ba-pipe"
}
```

### 字段说明

- `alert_id`: 预警唯一标识符
- `task_id`: 关联的任务ID
- `task_name`: 任务名称
- `task_source_name`: 视频源名称
- `task_source_url`: 视频源URL
- `alert_time`: 预警发生时间（ISO格式）
- `alert_type`: 预警类型（如phone_detection、person_detection等）
- `alert_message`: 预警描述信息
- `alert_image`: 预警截图文件路径
- `alert_image_ori`: 原始截图文件路径
- `alert_video`: 预警视频文件路径（如果有录制）
- `timestamp`: Unix时间戳
- `source`: 消息来源标识

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
4. **幂等性**: 确保重复消息不会造成重复处理

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
