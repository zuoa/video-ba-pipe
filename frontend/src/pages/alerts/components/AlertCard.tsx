import React from 'react';
import { Card, Tooltip } from 'antd';
import {
  UserOutlined,
  MobileOutlined,
  WarningOutlined,
  InfoCircleOutlined,
  CloseCircleOutlined,
  VideoCameraOutlined,
  ClockCircleOutlined,
  PlayCircleOutlined,
  ApartmentOutlined,
  FireOutlined,
  AppstoreOutlined,
  RightOutlined,
} from '@ant-design/icons';
import { ALERT_TYPE_CONFIG, Alert, Task } from '../types';
import RelativeTime from './RelativeTime';
import { appPalette } from '@/theme';
import './AlertCard.css';

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
  critical: <FireOutlined />,
  person_detection: <UserOutlined />,
  phone_detection_2stage: <MobileOutlined />,
};

const ALERT_COLORS: Record<string, { primary: string; bg: string }> = {
  warning: {
    primary: appPalette.warning,
    bg: appPalette.warningSoft,
  },
  error: {
    primary: appPalette.danger,
    bg: appPalette.dangerSoft,
  },
  info: {
    primary: appPalette.info,
    bg: appPalette.infoSoft,
  },
  critical: {
    primary: appPalette.danger,
    bg: appPalette.dangerSoft,
  },
  person_detection: {
    primary: appPalette.info,
    bg: appPalette.infoSoft,
  },
  phone_detection_2stage: {
    primary: appPalette.warning,
    bg: appPalette.warningSoft,
  },
};

// 获取默认颜色
const DEFAULT_COLOR = {
  primary: appPalette.info,
  bg: appPalette.infoSoft,
};

const AlertCard: React.FC<AlertCardProps> = ({ alert, task, onClick }) => {
  const alertType = alert.alert_type.toLowerCase();
  const taskName = task?.name || `任务 #${alert.task_id}`;
  const alertIcon = ALERT_ICONS[alertType] || <InfoCircleOutlined />;
  const colorScheme = ALERT_COLORS[alertType] || DEFAULT_COLOR;
  const alertTypeLabel = ALERT_TYPE_CONFIG[alertType]?.label || alert.alert_type.replace(/_/g, ' ');
  const workflowName = alert.workflow_name || (alert.workflow_id ? `编排 #${alert.workflow_id}` : '未关联编排');
  const cardStyle = {
    '--alert-color': colorScheme.primary,
    '--alert-soft': colorScheme.bg,
  } as React.CSSProperties;

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if ((event.key === 'Enter' || event.key === ' ') && onClick) {
      event.preventDefault();
      onClick();
    }
  };

  return (
    <Card
      hoverable
      onClick={onClick}
      onKeyDown={handleKeyDown}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      aria-label={onClick ? `查看${taskName}的${alertTypeLabel}告警详情` : undefined}
      className="alert-record-card"
      style={cardStyle}
      styles={{ body: { padding: 0 } }}
      cover={(
        <div className="alert-card-media">
          {alert.alert_image_url || alert.alert_image ? (
            <img
              alt={`${taskName}的${alertTypeLabel}告警画面`}
              src={alert.alert_image_url || `/api/image/frames/${alert.alert_image}`}
              loading="lazy"
              decoding="async"
              className="alert-card-image"
            />
          ) : (
            <div className="alert-card-image-placeholder" aria-label="暂无告警图片">
              {alertIcon}
            </div>
          )}

          <div className="alert-card-media-shade" aria-hidden="true" />
          <div className="alert-card-media-topline">
            <span className="alert-card-type">
              {alertIcon}
              <span>{alertTypeLabel}</span>
            </span>
            {alert.alert_video ? (
              <span className="alert-card-video-badge">
                <PlayCircleOutlined />
                有录像
              </span>
            ) : null}
          </div>
        </div>
      )}
    >
      <div className="alert-card-content">
        <div className="alert-card-source">
          <span className="alert-card-source-icon" aria-hidden="true">
            <VideoCameraOutlined />
          </span>
          <div className="alert-card-source-copy">
            <span className="alert-card-label">视频源</span>
            <Tooltip title={taskName} mouseEnterDelay={0.5}>
              <strong className="alert-card-source-name">{taskName}</strong>
            </Tooltip>
          </div>
        </div>

        <div className="alert-card-meta">
          <div className="alert-card-meta-row">
            <ClockCircleOutlined aria-hidden="true" />
            <span className="alert-card-meta-label">发生时间</span>
            <span className="alert-card-meta-value alert-card-time">
              <RelativeTime time={alert.alert_time} showFullTime />
            </span>
          </div>
          <div className="alert-card-meta-row">
            <ApartmentOutlined aria-hidden="true" />
            <span className="alert-card-meta-label">算法编排</span>
            <Tooltip title={workflowName} mouseEnterDelay={0.5}>
              <span className="alert-card-meta-value">{workflowName}</span>
            </Tooltip>
          </div>
        </div>

        <div className="alert-card-footer">
          <span className="alert-card-frame-count">
            <AppstoreOutlined aria-hidden="true" />
            检测 {alert.detection_count || 0} 帧
          </span>
          <span className="alert-card-detail">
            查看详情
            <RightOutlined aria-hidden="true" />
          </span>
        </div>
      </div>
    </Card>
  );
};

export default AlertCard;
