import React, { useMemo } from 'react';
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
  alert_image_url?: string;
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
  viewAllLabel?: string;
  loading?: boolean;
  compact?: boolean;
  minimal?: boolean;
  maxItems?: number;
}

const RecentAlertCard: React.FC<RecentAlertCardProps> = ({
  title,
  icon,
  alerts,
  tasks,
  viewAllPath,
  viewAllLabel = '查看告警',
  loading = false,
  compact = false,
  minimal = false,
  maxItems = 5,
}) => {
  const taskById = useMemo(
    () => new Map(tasks.map((task) => [task.id, task])),
    [tasks],
  );
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
          {!minimal && <BellOutlined className="loading-icon" spin />}
          <p>正在加载最新告警…</p>
        </div>
      );
    }
    return (
      <div className="recent-alerts-empty">
        {!minimal && <BellOutlined className="empty-icon" />}
        <p>当前没有新的告警记录</p>
      </div>
    );
  };

  return (
    <div
      className={[
        'recent-alerts-card',
        compact ? 'recent-alerts-card--compact' : '',
        minimal ? 'recent-alerts-card--minimal' : '',
      ].filter(Boolean).join(' ')}
    >
      <div className="recent-alerts-header">
        <h3 className="recent-alerts-title">
          <span className="title-icon">{icon}</span>
          {title}
        </h3>
        {viewAllPath && (
          <a href={viewAllPath} className="view-all-link">
            {viewAllLabel}
          </a>
        )}
      </div>
      <div className="recent-alerts-list">
        {alerts.length === 0 ? (
          renderEmpty()
        ) : (
          alerts.slice(0, maxItems).map((alert) => {
            const task = taskById.get(alert.task_id);
            const taskName = task
              ? `${task.name} #${task.source_code}`
              : `视频源 #${alert.task_id}`;
            const typeConfig = getAlertTypeConfig(alert.alert_type);
            const time = dayjs(alert.alert_time).format('MM-DD HH:mm');
            const imageUrl = alert.alert_image_url
              || (alert.alert_image ? `/api/image/frames/${alert.alert_image}` : '');

            const itemContent = (
              <>
                {!minimal && (imageUrl ? (
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
                ))}
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
              </>
            );

            return viewAllPath ? (
              <a
                key={alert.id}
                href={viewAllPath}
                className="recent-alert-item"
                aria-label={`查看 ${taskName} 的 ${alert.alert_type} 告警`}
              >
                {itemContent}
              </a>
            ) : (
              <div key={alert.id} className="recent-alert-item">
                {itemContent}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};

export default RecentAlertCard;
