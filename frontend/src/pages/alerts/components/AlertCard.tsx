import React from 'react';
import { Card, Tag, Badge, Space, Typography } from 'antd';
import {
  EyeOutlined,
  UserOutlined,
  MobileOutlined,
  WarningOutlined,
  InfoCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
  VideoCameraOutlined,
  ClockCircleOutlined,
  PlayCircleOutlined,
} from '@ant-design/icons';
import { Alert, Task } from '../types';
import RelativeTime from './RelativeTime';

const { Text } = Typography;

interface AlertCardProps {
  alert: Alert;
  task?: Task;
  onClick?: () => void;
}

// 告警类型图标映射
const ALERT_ICONS: Record<string, React.ReactNode> = {
  warning: <WarningOutlined />,
  error: <CloseCircleOutlined />,
  info: <InfoCircleOutlined />,
  critical: <ExclamationCircleOutlined />,
  person_detection: <UserOutlined />,
  phone_detection_2stage: <MobileOutlined />,
};

// 告警类型颜色映射
const ALERT_COLORS: Record<string, string> = {
  warning: 'orange',
  error: 'red',
  info: 'blue',
  critical: 'purple',
  person_detection: 'blue',
  phone_detection_2stage: 'orange',
};

const AlertCard: React.FC<AlertCardProps> = ({ alert, task, onClick }) => {
  const taskName = task?.name || `任务 #${alert.task_id}`;
  const alertIcon = ALERT_ICONS[alert.alert_type] || <InfoCircleOutlined />;
  const alertColor = ALERT_COLORS[alert.alert_type] || 'blue';

  return (
    <Card
      hoverable
      onClick={onClick}
      styles={{
        body: { padding: 16 },
      }}
      cover={
        <div style={{
          position: 'relative',
          aspectRatio: 16 / 9,
          background: '#f5f5f5',
          overflow: 'hidden',
        }}>
          {alert.alert_image ? (
            <img
              alt="alert"
              src={`/api/image/frames/${alert.alert_image}`}
              style={{ width: '100%', height: '100%', objectFit: 'cover' }}
            />
          ) : (
            <div style={{
              width: '100%',
              height: '100%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 48,
              color: '#d9d9d9',
            }}>
              {alertIcon}
            </div>
          )}
          <Tag
            color={alertColor}
            style={{
              position: 'absolute',
              top: 8,
              right: 8,
            }}
          >
            {alert.alert_type}
          </Tag>
        </div>
      }
    >
      <Space direction="vertical" style={{ width: '100%' }} size="small">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Space>
            <VideoCameraOutlined />
            <Text strong ellipsis>{taskName}</Text>
          </Space>
          {alert.detection_count > 1 && (
            <Badge count={alert.detection_count} color={alertColor} />
          )}
        </div>

        {alert.workflow_id && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            工作流: {alert.workflow_name || '未关联'}
          </Text>
        )}

        <Text
          type="secondary"
          ellipsis={{ rows: 2 }}
          style={{
            fontSize: 12,
            background: '#fafafa',
            padding: '8px 12px',
            borderRadius: 4,
            display: 'block',
          }}
        >
          {alert.alert_message}
        </Text>

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Space size="small">
            <ClockCircleOutlined />
            <RelativeTime time={alert.alert_time} showFullTime />
          </Space>
          {alert.alert_video && (
            <Tag icon={<PlayCircleOutlined />} color="blue">
              视频
            </Tag>
          )}
        </div>
      </Space>
    </Card>
  );
};

export default AlertCard;
