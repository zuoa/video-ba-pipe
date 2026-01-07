import React, { useState } from 'react';
import { Modal, Descriptions, Tag, Image, Space, Button, List, Typography } from 'antd';
import {
  LeftOutlined,
  RightOutlined,
  CloseOutlined,
  InfoCircleOutlined,
  VideoCameraOutlined,
  PlayCircleOutlined,
} from '@ant-design/icons';
import { Alert, Task, DetectionImage } from '../types';

const { Title, Text } = Typography;

interface AlertDetailModalProps {
  visible: boolean;
  alert: Alert | null;
  tasks: Task[];
  currentIndex: number;
  total: number;
  onClose: () => void;
  onNavigate: (direction: 'prev' | 'next') => void;
}

const AlertDetailModal: React.FC<AlertDetailModalProps> = ({
  visible,
  alert,
  tasks,
  currentIndex,
  total,
  onClose,
  onNavigate,
}) => {
  const [imagePreview, setImagePreview] = useState<string>('');

  if (!alert) return null;

  const task = tasks.find(t => t.id === alert.task_id);
  const taskName = task?.name || `任务 #${alert.task_id}`;

  // 解析窗口统计
  const windowStats = typeof alert.window_stats === 'string'
    ? JSON.parse(alert.window_stats || '{}')
    : alert.window_stats || {};

  // 解析检测图片
  const detectionImages = typeof alert.detection_images === 'string'
    ? JSON.parse(alert.detection_images || '[]')
    : alert.detection_images || [];

  return (
    <>
      <Modal
        open={visible}
        onCancel={onClose}
        footer={null}
        width={800}
        title={
          <Space style={{ width: '100%', justifyContent: 'space-between' }}>
            <Space>
              <InfoCircleOutlined />
              <span>告警详情</span>
              <Text type="secondary">({currentIndex + 1} / {total})</Text>
            </Space>
            <Space>
              <Button
                icon={<LeftOutlined />}
                onClick={() => onNavigate('prev')}
                disabled={currentIndex === 0}
              />
              <Button
                icon={<RightOutlined />}
                onClick={() => onNavigate('next')}
                disabled={currentIndex === total - 1}
              />
            </Space>
          </Space>
        }
      >
        <Space direction="vertical" style={{ width: '100%' }} size="large">
          {/* 基本信息 */}
          <Descriptions bordered column={2} size="small">
            <Descriptions.Item label="任务">{taskName}</Descriptions.Item>
            <Descriptions.Item label="告警类型">
              <Tag color="blue">{alert.alert_type}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="告警时间">
              {new Date(alert.alert_time).toLocaleString('zh-CN')}
            </Descriptions.Item>
            <Descriptions.Item label="检测数量">{alert.detection_count}</Descriptions.Item>
            <Descriptions.Item label="告警消息" span={2}>
              {alert.alert_message}
            </Descriptions.Item>
          </Descriptions>

          {/* 窗口统计 */}
          {alert.detection_count > 1 && Object.keys(windowStats).length > 0 && (
            <>
              <Title level={5}>时间窗口检测</Title>
              <Space>
                <Tag>检测帧数: {windowStats.detection_count} / {windowStats.total_count}</Tag>
                <Tag>检测比例: {(windowStats.detection_ratio * 100).toFixed(1)}%</Tag>
                <Tag>最大连续: {windowStats.max_consecutive} 次</Tag>
              </Space>

              {detectionImages.length > 0 && (
                <>
                  <Title level={5}>检测图片序列</Title>
                  <Image.PreviewGroup
                    preview={{
                      current: imagePreview,
                      onChange: setImagePreview,
                    }}
                  >
                    <List
                      grid={{ gutter: 8, column: 4 }}
                      dataSource={detectionImages}
                      renderItem={(img: DetectionImage | any, index: number) => (
                        <List.Item>
                          <Image
                            width="100%"
                            src={`/api/image/frames/${img.image_path}`}
                            alt={`检测 ${index + 1}`}
                          />
                          <Text type="secondary" style={{ fontSize: 11 }}>
                            第 {index + 1} 次
                          </Text>
                        </List.Item>
                      )}
                    />
                  </Image.PreviewGroup>
                </>
              )}
            </>
          )}

          {/* 告警媒体 */}
          {(alert.alert_image || alert.alert_image_ori || alert.alert_video) && (
            <>
              <Title level={5}>告警媒体</Title>
              <Space direction="horizontal" wrap>
                {alert.alert_image && (
                  <div>
                    <Text type="secondary">告警图片</Text>
                    <Image
                      width={200}
                      src={`/api/image/frames/${alert.alert_image}`}
                      style={{ marginTop: 8 }}
                    />
                  </div>
                )}
                {alert.alert_image_ori && (
                  <div>
                    <Text type="secondary">原始图片</Text>
                    <Image
                      width={200}
                      src={`/api/image/frames/${alert.alert_image_ori}`}
                      style={{ marginTop: 8 }}
                    />
                  </div>
                )}
                {alert.alert_video && (
                  <div>
                    <Text type="secondary">告警视频</Text>
                    <video
                      width={200}
                      controls
                      preload="metadata"
                      src={`/api/video/videos/${alert.alert_video}`}
                      style={{ marginTop: 8 }}
                    />
                  </div>
                )}
              </Space>
            </>
          )}
        </Space>
      </Modal>
    </>
  );
};

export default AlertDetailModal;
