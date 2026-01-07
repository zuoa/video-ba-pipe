import React from 'react';
import { Tag } from 'antd';
import {
  BellOutlined,
  PictureOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import './RecentAlertCard.css';

export interface Alert {
  id: number;
  task_id: number;
  alert_type: string;
  alert_message: string;
  alert_time: string;
  alert_image?: string;
  alert_video?: string;
  detection_count?: number;
}

export interface Task {
  id: number;
  name: string;
  source_code: string;
}

export interface RecentAlertCardProps {
  title: string;
  icon: React.ReactNode;
  alerts: Alert[];
  tasks: Task[];
  viewAllPath?: string;
  loading?: boolean;
}

const RecentAlertCard: React.FC<RecentAlertCardProps> = ({
  title,
  icon,
  alerts,
  tasks,
  viewAllPath,
  loading = false,
}) => {
  const getAlertTypeConfig = (type: string) => {
    const typeMap: Record<string, { color: string; bgColor: string }> = {
      warning: { color: '#faad14', bgColor: '#fff7e6' },
      error: { color: '#ff4d4f', bgColor: '#fff1f0' },
      info: { color: '#1677ff', bgColor: '#e6f4ff' },
      critical: { color: '#000000', bgColor: '#f5f5f5' },
    };
    return typeMap[type.toLowerCase()] || typeMap.info;
  };

  const renderEmpty = () => {
    if (loading) {
      return (
        <div className="recent-alerts-empty">
          <BellOutlined className="loading-icon" spin />
          <p>加载中...</p>
        </div>
      );
    }
    return (
      <div className="recent-alerts-empty">
        <BellOutlined className="empty-icon" />
        <p>暂无告警</p>
      </div>
    );
  };

  const handleAlertClick = () => {
    if (viewAllPath) {
      window.location.href = viewAllPath;
    }
  };

  return (
    <div className="recent-alerts-card">
      <div className="recent-alerts-header">
        <h3 className="recent-alerts-title">
          <span className="title-icon">{icon}</span>
          {title}
        </h3>
        {viewAllPath && (
          <a href={viewAllPath} className="view-all-link">
            查看全部
          </a>
        )}
      </div>
      <div className="recent-alerts-list">
        {alerts.length === 0 ? (
          renderEmpty()
        ) : (
          alerts.slice(0, 5).map((alert) => {
            const task = tasks.find((t) => t.id === alert.task_id);
            const taskName = task
              ? `${task.name} #${task.source_code}`
              : `任务 #${alert.task_id}`;
            const typeConfig = getAlertTypeConfig(alert.alert_type);
            const time = dayjs(alert.alert_time).format('MM-DD HH:mm');
            const imageUrl = alert.alert_image
              ? `/api/image/frames/${alert.alert_image}`
              : '';

            return (
              <div
                key={alert.id}
                className="recent-alert-item"
                onClick={() => handleAlertClick(alert)}
              >
                {imageUrl ? (
                  <div className="alert-image-wrapper">
                    <img
                      src={imageUrl}
                      alt="Alert"
                      className="alert-image"
                      onError={(e) => {
                        const target = e.target as HTMLImageElement;
                        target.style.display = 'none';
                        const placeholder =
                          target.nextElementSibling as HTMLElement;
                        if (placeholder) placeholder.style.display = 'flex';
                      }}
                    />
                    <div
                      className="alert-image-placeholder"
                      style={{ display: 'none' }}
                    >
                      <PictureOutlined />
                    </div>
                  </div>
                ) : (
                  <div
                    className="alert-icon-placeholder"
                    style={{
                      background: `linear-gradient(135deg, ${typeConfig.bgColor} 0%, ${typeConfig.color}20 100%)`,
                    }}
                  >
                    <BellOutlined
                      style={{
                        color: typeConfig.color,
                        fontSize: '16px',
                      }}
                    />
                  </div>
                )}
                <div className="alert-content-wrapper">
                  <div className="alert-content">
                    <p className="alert-task-name">{taskName}</p>
                    <div className="alert-meta">
                      <ClockCircleOutlined className="time-icon" />
                      <span className="alert-time">{time}</span>
                    </div>
                  </div>
                  <Tag
                    className="alert-type-tag"
                    style={{
                      background: typeConfig.bgColor,
                      color: typeConfig.color,
                      border: `1px solid ${typeConfig.color}40`,
                    }}
                  >
                    {alert.alert_type}
                  </Tag>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};

export default RecentAlertCard;
