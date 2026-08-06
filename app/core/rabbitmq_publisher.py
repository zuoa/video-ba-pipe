"""
RabbitMQ预警发布器模块
用于将预警信息发布到RabbitMQ队列
"""

import json
import logging
import time
from typing import Any, Dict, Optional

try:
    import pika
    from pika.exceptions import AMQPChannelError, AMQPConnectionError
except ImportError:  # pragma: no cover - optional dependency in test/runtime subsets
    pika = None

    class AMQPConnectionError(Exception):
        pass

    class AMQPChannelError(Exception):
        pass

from app.config import (
    RABBITMQ_HOST, RABBITMQ_PORT, RABBITMQ_USER, RABBITMQ_PASSWORD,
    RABBITMQ_VHOST, RABBITMQ_ALERT_EXCHANGE,
    RABBITMQ_ALERT_ROUTING_KEY, RABBITMQ_CONNECTION_TIMEOUT, RABBITMQ_ENABLED,
    RABBITMQ_EXCHANGE_TYPE
)
from app.core.node_identity import get_hostname, get_node_id
from app.core.public_media_config import build_public_media_url, get_public_media_config

logger = logging.getLogger(__name__)


class RabbitMQPublisher:
    """RabbitMQ预警发布器"""
    
    def __init__(self):
        self.connection = None
        self.channel = None
        self.connected = False
        
    def connect(self) -> bool:
        """
        连接到RabbitMQ服务器
        
        Returns:
            bool: 连接是否成功
        """
        if not RABBITMQ_ENABLED:
            logger.info("RabbitMQ功能未启用，跳过连接")
            return False

        if pika is None:
            logger.warning("pika 未安装，无法连接 RabbitMQ")
            return False
            
        try:
            # 构建连接参数
            credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASSWORD)
            parameters = pika.ConnectionParameters(
                host=RABBITMQ_HOST,
                port=RABBITMQ_PORT,
                virtual_host=RABBITMQ_VHOST,
                credentials=credentials,
                connection_attempts=3,
                retry_delay=2,
                socket_timeout=RABBITMQ_CONNECTION_TIMEOUT
            )
            
            # 建立连接
            self.connection = pika.BlockingConnection(parameters)
            self.channel = self.connection.channel()
            
            # 只声明交换机：消费队列由消费端自行声明并绑定。
            # 生产者不应声明自己的消费队列——否则在无消费者时消息会无限堆积，
            # 最终耗尽 broker 内存/磁盘。对无队列绑定的消息，broker 会直接丢弃，
            # 消费端连上后自行声明队列即可正常接收（参见 scripts/*_consumer*.py）。
            self.channel.exchange_declare(
                exchange=RABBITMQ_ALERT_EXCHANGE,
                exchange_type=RABBITMQ_EXCHANGE_TYPE,
                durable=True
            )

            self.connected = True
            logger.info(f"成功连接到RabbitMQ服务器 {RABBITMQ_HOST}:{RABBITMQ_PORT}")
            return True
            
        except AMQPConnectionError as e:
            logger.error(f"连接RabbitMQ失败: {e}")
            self.connected = False
            return False
        except Exception as e:
            logger.error(f"初始化RabbitMQ连接时发生未知错误: {e}")
            self.connected = False
            return False
    
    def disconnect(self):
        """断开RabbitMQ连接"""
        try:
            if self.channel and not self.channel.is_closed:
                self.channel.close()
            if self.connection and not self.connection.is_closed:
                self.connection.close()
            self.connected = False
            logger.info("已断开RabbitMQ连接")
        except Exception as e:
            logger.warning(f"断开RabbitMQ连接时发生错误: {e}")
    
    def is_connected(self) -> bool:
        """检查连接状态"""
        if not self.connected or not self.connection or not self.channel:
            return False
        
        try:
            return not self.connection.is_closed and not self.channel.is_closed
        except Exception:
            return False
    
    def publish_alert(self, alert_data: Dict[str, Any]) -> bool:
        """
        发布预警消息到RabbitMQ
        
        Args:
            alert_data: 预警数据字典
            
        Returns:
            bool: 发布是否成功
        """
        if not RABBITMQ_ENABLED:
            logger.debug("RabbitMQ功能未启用，跳过预警发布")
            return False

        if pika is None:
            logger.warning("pika 未安装，跳过 RabbitMQ 预警发布")
            return False
            
        # 检查连接状态，如果断开则尝试重连
        if not self.is_connected():
            logger.info("RabbitMQ连接已断开，尝试重新连接...")
            if not self.connect():
                logger.error("重新连接RabbitMQ失败，无法发布预警消息")
                return False
        
        try:
            # 准备消息
            message = json.dumps(alert_data, ensure_ascii=False, default=str)
            
            # 根据预警类型生成routing key
            if RABBITMQ_EXCHANGE_TYPE == 'topic':
                # Topic模式：routing_key 形如 video.alert.{node_id}.{alert_type}，
                # 消费端可按节点订阅（video.alert.{node_id}.*）或全量订阅（video.alert.#）。
                # node_id 防御性去 '.'，避免破坏 routing_key 分段。
                alert_type = alert_data.get('alert_type', 'unknown').lower()
                node_id = str(alert_data.get('node_id') or 'unknown').replace('.', '-')
                routing_key = f"video.alert.{node_id}.{alert_type}"
            else:
                # Direct模式：使用配置的routing key
                routing_key = RABBITMQ_ALERT_ROUTING_KEY
            
            # 发布消息
            self.channel.basic_publish(
                exchange=RABBITMQ_ALERT_EXCHANGE,
                routing_key=routing_key,
                body=message,
                properties=pika.BasicProperties(
                    delivery_mode=2,  # 消息持久化
                    timestamp=int(time.time()),
                    content_type='application/json',
                    content_encoding='utf-8'
                )
            )
            
            logger.info(f"成功发布预警消息到RabbitMQ: {alert_data.get('alert_type', 'Unknown')}")
            return True
            
        except AMQPChannelError as e:
            logger.error(f"发布预警消息时发生通道错误: {e}")
            self.connected = False
            return False
        except AMQPConnectionError as e:
            logger.error(f"发布预警消息时发生连接错误: {e}")
            self.connected = False
            return False
        except Exception as e:
            logger.error(f"发布预警消息时发生未知错误: {e}")
            return False
    
    def __enter__(self):
        """上下文管理器入口"""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.disconnect()


# 全局RabbitMQ发布器实例
rabbitmq_publisher = RabbitMQPublisher()


def publish_alert_to_rabbitmq(alert_data: Dict[str, Any]) -> bool:
    """
    便捷函数：发布预警消息到RabbitMQ
    
    Args:
        alert_data: 预警数据字典
        
    Returns:
        bool: 发布是否成功
    """
    return rabbitmq_publisher.publish_alert(alert_data)


def format_alert_message(alert) -> Dict[str, Any]:
    """
    格式化Alert对象为RabbitMQ消息格式

    Args:
        alert: Alert数据库模型实例

    Returns:
        Dict[str, Any]: 格式化后的预警消息
    """
    media_config = get_public_media_config()
    node_id = get_node_id()
    message = {
        'alert_id': alert.id,
        # 集群下 alert_id（DB 自增）会跨机器撞号，external_alert_id 全局唯一，供消费端去重
        'external_alert_id': f"{node_id}-{alert.id}",
        # 来源机器标识，集群/MQ 推送时用于区分是哪台盒子发出的
        'node_id': node_id,
        'host': get_hostname(),
        'source_id': alert.video_source.id,
        'source_name': alert.video_source.name,
        'source_code': alert.video_source.source_code,
        'alert_time': alert.alert_time.isoformat() if hasattr(alert.alert_time, 'isoformat') else str(alert.alert_time),
        'alert_type': alert.alert_type,
        'alert_message': alert.alert_message,
        'alert_image': alert.alert_image,
        'alert_image_url': build_public_media_url('image', alert.alert_image, config=media_config),
        'alert_image_ori': alert.alert_image_ori,
        'alert_image_ori_url': build_public_media_url('image', alert.alert_image_ori, config=media_config),
        'alert_video': alert.alert_video,
        'alert_video_url': build_public_media_url('video', alert.alert_video, config=media_config),
        'timestamp': time.time(),
        'source': 'video-ba-pipe'
    }

    # 添加 workflow 信息（如果存在）
    if alert.workflow:
        message['workflow_id'] = alert.workflow.id
        message['workflow_name'] = alert.workflow.name

    return message
