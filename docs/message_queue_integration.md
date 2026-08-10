# 消息队列接入

系统支持 MQTT 和 RabbitMQ 单通道发布，全新安装默认选择 MQTT。启用状态、提供方和全部客户端连接参数均在“系统设置 → 消息队列”中保存，不读取环境变量。

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

## 消息体与去重

两个协议使用相同 JSON 消息体。集群消费端应使用 `external_alert_id` 去重，并使用 `node_id` 识别告警来源。MQTT QoS 1 允许重复投递，因此消费端必须保证去重处理具有幂等性。
