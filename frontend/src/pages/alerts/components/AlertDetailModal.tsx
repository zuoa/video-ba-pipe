import React, { useState } from 'react';
import { Modal, Descriptions, Tag, Image, Space, Button, List, Typography } from 'antd';
import {
  LeftOutlined,
  RightOutlined,
  CloseOutlined,
  InfoCircleOutlined,
  VideoCameraOutlined,
  PlayCircleOutlined,
  FileImageOutlined,
  ApartmentOutlined,
} from '@ant-design/icons';
import { Alert, Task, DetectionImage } from '../types';
import './AlertDetailModal.css';

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
        width={900}
        className="alertDetailModal"
        title={
          <Space style={{ width: '100%', justifyContent: 'space-between' }} size="large">
            <Space size="middle">
              <InfoCircleOutlined style={{ fontSize: 18 }} />
              <span>告警详情</span>
              <Text style={{ color: 'rgba(255, 255, 255, 0.85)' }}>
                ({currentIndex + 1} / {total})
              </Text>
            </Space>
            <Space className="modalNavButtons">
              <Button
                icon={<LeftOutlined />}
                onClick={() => onNavigate('prev')}
                disabled={currentIndex === 0}
              >
                上一条
              </Button>
              <Button
                icon={<RightOutlined />}
                onClick={() => onNavigate('next')}
                disabled={currentIndex === total - 1}
              >
                下一条
              </Button>
            </Space>
          </Space>
        }
      >
        <div className="alertContent">
          {/* 基本信息 */}
          <div className="infoCard">
            <Descriptions bordered={false} column={2} size="small">
              <Descriptions.Item label="任务">{taskName}</Descriptions.Item>
              <Descriptions.Item label="告警类型">
                <Tag color="blue" style={{ margin: 0 }}>{alert.alert_type}</Tag>
              </Descriptions.Item>
              {alert.workflow_id && (
                <Descriptions.Item label={<span><ApartmentOutlined /> 流程编排</span>}>
                  <Tag color="purple" icon={<ApartmentOutlined />} style={{ margin: 0 }}>
                    {alert.workflow_name || `流程编排 #${alert.workflow_id}`}
                  </Tag>
                </Descriptions.Item>
              )}
              <Descriptions.Item label="告警时间">
                {new Date(alert.alert_time).toLocaleString('zh-CN')}
              </Descriptions.Item>
              <Descriptions.Item label="检测帧数">
                <Tag color="green" style={{ margin: 0 }}>{alert.detection_count} 帧</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="告警消息" span={2}>
                <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  {alert.alert_message}
                </div>
              </Descriptions.Item>
            </Descriptions>
          </div>

          {/* 窗口统计 */}
          {Object.keys(windowStats).length > 0 && (
            <>
              <Title level={5} className="sectionTitle">
                <VideoCameraOutlined />
                时间窗口检测统计
              </Title>
              <div className="statsTags">
                <Tag color="blue">
                  检测帧数: <strong>{windowStats.detection_count}</strong> / {windowStats.total_count}
                </Tag>
                <Tag color="purple">
                  检测比例: <strong>{(windowStats.detection_ratio * 100).toFixed(1)}%</strong>
                </Tag>
                <Tag color="orange">
                  最大连续: <strong>{windowStats.max_consecutive}</strong> 次
                </Tag>
              </div>

              {detectionImages.length > 0 && (
                <>
                  <Title level={5} className="sectionTitle">
                    <FileImageOutlined />
                    检测图片序列
                  </Title>
                  <Image.PreviewGroup
                    preview={{
                      current: imagePreview,
                      onChange: setImagePreview,
                    }}
                  >
                    <div className="detectionImageGrid">
                      {detectionImages.map((img: DetectionImage | any, index: number) => (
                        <div key={index} className="detectionImageItem">
                          <Image
                            src={`/api/image/frames/${img.image_path}`}
                            alt={`检测 ${index + 1}`}
                            preview={{ title: `第 ${index + 1} 次检测` }}
                          />
                          <div className="detectionImageIndex">第 {index + 1} 次</div>
                        </div>
                      ))}
                    </div>
                  </Image.PreviewGroup>
                </>
              )}
            </>
          )}

          {/* 告警媒体 */}
          {(alert.alert_image || alert.alert_image_ori || alert.alert_video) && (
            <>
              <Title level={5} className="sectionTitle">
                <PlayCircleOutlined />
                告警媒体资源
              </Title>
              <div className="mediaSection">
                {alert.alert_image && (
                  <div className="mediaCard">
                    <div className="mediaCardLabel">告警图片</div>
                    <div className="mediaCardImage">
                      <Image
                        src={`/api/image/frames/${alert.alert_image}`}
                        alt="告警图片"
                        preview={{ title: '告警图片' }}
                      />
                    </div>
                  </div>
                )}
                {alert.alert_image_ori && (
                  <div className="mediaCard">
                    <div className="mediaCardLabel">原始图片</div>
                    <div className="mediaCardImage">
                      <Image
                        src={`/api/image/frames/${alert.alert_image_ori}`}
                        alt="原始图片"
                        preview={{ title: '原始图片' }}
                      />
                    </div>
                  </div>
                )}
                {alert.alert_video && (
                  <div className="mediaCard">
                    <div className="mediaCardLabel">告警视频</div>
                    <div className="mediaCardVideo">
                      <video
                        controls
                        preload="metadata"
                        src={`/api/video/videos/${alert.alert_video}`}
                      />
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </Modal>
    </>
  );
};

export default AlertDetailModal;
