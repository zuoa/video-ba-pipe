# RabbitMQ 兼容接入

RabbitMQ 作为 MQTT 主通道之外的兼容提供方保留。启用状态、连接参数、交换机和 Routing Key 均在“系统设置 → 消息队列”页面配置，不读取环境变量。

系统采用单通道模式：选择 RabbitMQ 后只发布 RabbitMQ，不再同时发布 MQTT。生产者声明持久化交换机，但不声明消费队列；队列由消费端创建并绑定。

Topic 模式的 Routing Key 为：

```text
video.alert.{node_id}.{alert_type}
```

消费全部节点和类型时应绑定 `video.alert.#`。Direct 模式使用页面配置的固定 Routing Key。

消息 JSON 与 MQTT 完全一致。多节点部署应通过 `external_alert_id` 去重，并通过 `node_id` 识别来源。完整通用说明参阅 [message_queue_integration.md](message_queue_integration.md)。

配置完成后可运行：

```bash
python3 scripts/rabbitmq_consumer.py
```
