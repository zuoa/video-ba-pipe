#!/usr/bin/env python3
"""
Topic模式消费者示例
演示如何创建多个消费者来接收不同类型的预警消息
"""

import os
import sys
import json
import signal
import logging
from datetime import datetime

import pika

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.message_queue_config import get_message_queue_config
from app.core.rabbitmq_config import get_rabbitmq_config

_mq_config = get_message_queue_config()
_rabbitmq_config = get_rabbitmq_config()
RABBITMQ_HOST = _rabbitmq_config.host
RABBITMQ_PORT = _rabbitmq_config.port
RABBITMQ_USER = _rabbitmq_config.username
RABBITMQ_PASSWORD = _rabbitmq_config.password
RABBITMQ_VHOST = _rabbitmq_config.vhost
RABBITMQ_ENABLED = _mq_config.enabled and _mq_config.provider == "rabbitmq"

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TopicAlertConsumer:
    """Topic模式预警消费者"""
    
    def __init__(self, consumer_name, queue_name, topic_pattern):
        self.consumer_name = consumer_name
        self.queue_name = queue_name
        self.topic_pattern = topic_pattern
        self.connection = None
        self.channel = None
        self.running = False
        
    def connect(self):
        """连接到RabbitMQ"""
        if not RABBITMQ_ENABLED:
            logger.error("RabbitMQ功能未启用")
            return False
            
        try:
            credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASSWORD)
            parameters = pika.ConnectionParameters(
                host=RABBITMQ_HOST,
                port=RABBITMQ_PORT,
                virtual_host=RABBITMQ_VHOST,
                credentials=credentials,
                connection_attempts=3,
                retry_delay=2
            )
            
            self.connection = pika.BlockingConnection(parameters)
            self.channel = self.connection.channel()
            
            # 声明交换机（topic类型）
            self.channel.exchange_declare(
                exchange='video_alerts',
                exchange_type='topic',
                durable=True
            )
            
            # 声明专用队列
            self.channel.queue_declare(queue=self.queue_name, durable=True)
            
            # 绑定队列到交换机，使用topic模式
            self.channel.queue_bind(
                exchange='video_alerts',
                queue=self.queue_name,
                routing_key=self.topic_pattern
            )
            
            # 设置QoS
            self.channel.basic_qos(prefetch_count=1)
            
            logger.info(f"[{self.consumer_name}] 成功连接到RabbitMQ，监听模式: {self.topic_pattern}")
            return True
            
        except Exception as e:
            logger.error(f"[{self.consumer_name}] 连接RabbitMQ失败: {e}")
            return False
    
    def process_alert(self, ch, method, properties, body):
        """处理预警消息"""
        try:
            alert_data = json.loads(body.decode('utf-8'))
            
            print(f"\n[{self.consumer_name}] " + "=" * 50)
            print(f"🚨 收到预警消息 (Routing Key: {method.routing_key})")
            print(f"📋 预警ID: {alert_data.get('alert_id', 'N/A')}")
            print(f"📹 任务: {alert_data.get('task_name', 'N/A')}")
            print(f"🏷️  类型: {alert_data.get('alert_type', 'N/A')}")
            print(f"⏰ 时间: {alert_data.get('alert_time', 'N/A')}")
            print(f"💬 消息: {alert_data.get('alert_message', 'N/A')}")
            print(f"🕐 处理时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            # 根据消费者类型进行不同处理
            self.handle_specific_alert(alert_data)
            
            # 确认消息
            ch.basic_ack(delivery_tag=method.delivery_tag)
            print(f"✅ [{self.consumer_name}] 消息处理完成")
            
        except Exception as e:
            logger.error(f"[{self.consumer_name}] 处理消息时发生错误: {e}")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
    
    def handle_specific_alert(self, alert_data):
        """根据消费者类型处理特定预警"""
        alert_type = alert_data.get('alert_type', '').lower()
        
        if 'notification' in self.consumer_name.lower():
            # 通知消费者
            print(f"📱 [{self.consumer_name}] 发送预警通知: {alert_type}")
            
        elif 'analytics' in self.consumer_name.lower():
            # 分析消费者
            print(f"📊 [{self.consumer_name}] 分析预警数据: {alert_type}")
            
        elif 'logging' in self.consumer_name.lower():
            # 日志消费者
            print(f"📝 [{self.consumer_name}] 记录预警日志: {alert_type}")
            
        elif 'recording' in self.consumer_name.lower():
            # 录制消费者
            print(f"🎥 [{self.consumer_name}] 触发视频录制: {alert_type}")
            
        else:
            # 通用消费者
            print(f"🔔 [{self.consumer_name}] 处理通用预警: {alert_type}")
    
    def start_consuming(self):
        """开始消费消息"""
        if not self.connect():
            return False
        
        try:
            logger.info(f"[{self.consumer_name}] 开始监听队列: {self.queue_name}")
            
            # 设置消息处理函数
            self.channel.basic_consume(
                queue=self.queue_name,
                on_message_callback=self.process_alert
            )
            
            self.running = True
            self.channel.start_consuming()
            
        except KeyboardInterrupt:
            logger.info(f"[{self.consumer_name}] 收到停止信号")
            self.stop_consuming()
        except Exception as e:
            logger.error(f"[{self.consumer_name}] 消费消息时发生错误: {e}")
            return False
        
        return True
    
    def stop_consuming(self):
        """停止消费消息"""
        self.running = False
        try:
            if self.channel:
                self.channel.stop_consuming()
        except Exception:
            pass
        finally:
            try:
                if self.connection and not self.connection.is_closed:
                    self.connection.close()
            except Exception:
                pass


def create_consumers():
    """创建不同类型的消费者"""
    consumers = [
        # 通知消费者 - 接收所有预警
        TopicAlertConsumer("通知消费者", "notification_queue", "video.alert.*"),
        
        # 手机预警专用消费者
        TopicAlertConsumer("手机预警消费者", "phone_alert_queue", "video.alert.phone*"),
        
        # 人员预警专用消费者
        TopicAlertConsumer("人员预警消费者", "person_alert_queue", "video.alert.person*"),
        
        # 分析消费者 - 接收所有预警
        TopicAlertConsumer("分析消费者", "analytics_queue", "video.alert.*"),
        
        # 日志消费者 - 接收所有预警
        TopicAlertConsumer("日志消费者", "logging_queue", "video.alert.*"),
    ]
    
    return consumers


def signal_handler(signum, frame):
    """信号处理器"""
    logger.info("收到停止信号，正在关闭所有消费者...")
    global consumers
    for consumer in consumers:
        consumer.stop_consuming()


def main():
    """主函数"""
    global consumers
    
    print("🚀 启动Topic模式预警消费者集群")
    print(f"⏰ 启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    if not RABBITMQ_ENABLED:
        print("❌ RabbitMQ功能未启用")
        return
    
    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 创建消费者
    consumers = create_consumers()
    
    print(f"📋 创建了 {len(consumers)} 个消费者:")
    for consumer in consumers:
        print(f"  - {consumer.consumer_name}: {consumer.queue_name} -> {consumer.topic_pattern}")
    
    print("\n💡 Topic模式说明:")
    print("  - video.alert.* : 接收所有预警消息")
    print("  - video.alert.phone* : 只接收手机相关预警")
    print("  - video.alert.person* : 只接收人员相关预警")
    print("  - 一个消息可以同时发送给多个匹配的消费者")
    print("\n按 Ctrl+C 停止所有消费者")
    print("=" * 60)
    
    try:
        # 启动所有消费者（这里只启动第一个作为示例）
        # 在实际使用中，可以启动多个消费者进程
        if consumers:
            consumers[0].start_consuming()
    except Exception as e:
        logger.error(f"启动消费者失败: {e}")
    finally:
        print("\n👋 所有消费者已停止")


if __name__ == "__main__":
    consumers = []
    main()
