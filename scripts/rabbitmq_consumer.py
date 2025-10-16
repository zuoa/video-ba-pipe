#!/usr/bin/env python3
"""
RabbitMQ预警消息消费者示例
演示如何接收和处理视频预警消息
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

from app.config import (
    RABBITMQ_HOST, RABBITMQ_PORT, RABBITMQ_USER, RABBITMQ_PASSWORD,
    RABBITMQ_VHOST, RABBITMQ_ALERT_QUEUE, RABBITMQ_ENABLED,
    RABBITMQ_EXCHANGE_TYPE, RABBITMQ_ALERT_TOPIC_PATTERN
)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AlertConsumer:
    """预警消息消费者"""
    
    def __init__(self):
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
            
            # 声明队列（确保队列存在）
            self.channel.queue_declare(queue=RABBITMQ_ALERT_QUEUE, durable=True)
            
            # 如果是topic模式，需要重新绑定队列
            if RABBITMQ_EXCHANGE_TYPE == 'topic':
                try:
                    self.channel.queue_bind(
                        exchange='video_alerts',
                        queue=RABBITMQ_ALERT_QUEUE,
                        routing_key=RABBITMQ_ALERT_TOPIC_PATTERN
                    )
                    logger.info(f"Topic模式队列绑定成功: {RABBITMQ_ALERT_TOPIC_PATTERN}")
                except Exception as e:
                    logger.warning(f"队列绑定可能已存在: {e}")
            
            # 设置QoS，一次只处理一个消息
            self.channel.basic_qos(prefetch_count=1)
            
            logger.info(f"成功连接到RabbitMQ服务器 {RABBITMQ_HOST}:{RABBITMQ_PORT}")
            return True
            
        except Exception as e:
            logger.error(f"连接RabbitMQ失败: {e}")
            return False
    
    def disconnect(self):
        """断开连接"""
        try:
            if self.connection and not self.connection.is_closed:
                self.connection.close()
            logger.info("已断开RabbitMQ连接")
        except Exception as e:
            logger.warning(f"断开连接时发生错误: {e}")
    
    def process_alert(self, ch, method, properties, body):
        """处理预警消息"""
        try:
            # 解析消息
            alert_data = json.loads(body.decode('utf-8'))
            
            logger.info("=" * 60)
            logger.info("🚨 收到新的预警消息!")
            logger.info("=" * 60)
            
            # 显示预警信息
            print(f"📋 预警ID: {alert_data.get('alert_id', 'N/A')}")
            print(f"📹 任务名称: {alert_data.get('task_name', 'N/A')}")
            print(f"📷 摄像头: {alert_data.get('task_source_name', 'N/A')}( {alert_data.get('task_source_code', 'N/A')})")
            print(f"🔗 视频源: {alert_data.get('task_source_url', 'N/A')}")
            print(f"⏰ 预警时间: {alert_data.get('alert_time', 'N/A')}")
            print(f"🏷️  预警类型: {alert_data.get('alert_type', 'N/A')}")
            print(f"💬 预警消息: {alert_data.get('alert_message', 'N/A')}")
            print(f"🖼️  预警图片: {alert_data.get('alert_image', 'N/A')}")
            print(f"🎥 预警视频: {alert_data.get('alert_video', 'N/A')}")
            print(f"📡 消息来源: {alert_data.get('source', 'N/A')}")
            print(f"🕐 处理时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            # 这里可以添加自定义的处理逻辑
            # 例如：发送邮件、短信、推送到其他系统等
            self.handle_alert_notification(alert_data)
            
            # 确认消息处理完成
            ch.basic_ack(delivery_tag=method.delivery_tag)
            
            logger.info("✅ 预警消息处理完成")
            logger.info("=" * 60)
            
        except json.JSONDecodeError as e:
            logger.error(f"解析JSON消息失败: {e}")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        except Exception as e:
            logger.error(f"处理预警消息时发生错误: {e}")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
    
    def handle_alert_notification(self, alert_data):
        """处理预警通知（自定义逻辑）"""
        # 示例：根据预警类型进行不同的处理
        alert_type = alert_data.get('alert_type', '').lower()
        
        if 'phone' in alert_type:
            logger.info("📱 检测到手机使用预警 - 发送手机预警通知")
            # 这里可以添加手机预警的特定处理逻辑
        elif 'person' in alert_type:
            logger.info("👤 检测到人员预警 - 发送人员预警通知")
            # 这里可以添加人员预警的特定处理逻辑
        else:
            logger.info(f"🔔 处理通用预警: {alert_type}")
        
        # 示例：记录到文件
        log_file = f"alerts_{datetime.now().strftime('%Y%m%d')}.log"
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"{datetime.now().isoformat()} - {json.dumps(alert_data, ensure_ascii=False)}\n")
        
        logger.info(f"📝 预警记录已保存到文件: {log_file}")
    
    def start_consuming(self):
        """开始消费消息"""
        if not self.connect():
            return False
        
        try:
            logger.info(f"开始监听队列: {RABBITMQ_ALERT_QUEUE}")
            logger.info("等待预警消息... (按 Ctrl+C 停止)")
            
            # 设置消息处理函数
            self.channel.basic_consume(
                queue=RABBITMQ_ALERT_QUEUE,
                on_message_callback=self.process_alert
            )
            
            self.running = True
            self.channel.start_consuming()
            
        except KeyboardInterrupt:
            logger.info("收到停止信号，正在关闭...")
            self.stop_consuming()
        except Exception as e:
            logger.error(f"消费消息时发生错误: {e}")
            return False
        
        return True
    
    def stop_consuming(self):
        """停止消费消息"""
        self.running = False
        try:
            if self.channel:
                self.channel.stop_consuming()
        except Exception as e:
            logger.warning(f"停止消费时发生错误: {e}")
        finally:
            self.disconnect()


def signal_handler(signum, frame):
    """信号处理器"""
    logger.info("收到停止信号")
    global consumer
    if consumer:
        consumer.stop_consuming()


def main():
    """主函数"""
    global consumer
    
    print("🚀 启动RabbitMQ预警消息消费者")
    print(f"⏰ 启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📋 监听队列: {RABBITMQ_ALERT_QUEUE}")
    print(f"🔗 RabbitMQ服务器: {RABBITMQ_HOST}:{RABBITMQ_PORT}")
    print("=" * 60)
    
    if not RABBITMQ_ENABLED:
        print("❌ RabbitMQ功能未启用，请检查配置")
        return
    
    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 创建消费者并开始消费
    consumer = AlertConsumer()
    
    try:
        consumer.start_consuming()
    except Exception as e:
        logger.error(f"启动消费者失败: {e}")
    finally:
        print("\n👋 消费者已停止")


if __name__ == "__main__":
    consumer = None
    main()
