import React from 'react';
import './index.css';

const STATUS_LABELS: Record<string, string> = {
  STARTING: '启动中',
  RUNNING: '检测中',
  DRAINING: '排空中',
  STOPPED: '等待中',
  ERROR: '异常',
};

const ANIMATED_STATUSES = new Set(['STARTING', 'RUNNING', 'DRAINING', 'ERROR']);

export interface StatusBadgeProps {
  status: string;
  text?: string;
  size?: 'small' | 'default' | 'large';
}

const StatusBadge: React.FC<StatusBadgeProps> = ({
  status,
  text,
  size = 'default',
}) => {
  const getStatusConfig = (status: string) => {
    const statusMap: Record<string, {
      color: string;
      bgColor: string;
      borderColor: string;
      icon?: string;
    }> = {
      RUNNING: {
        color: '#389e0d',
        bgColor: '#f6ffed',
        borderColor: '#b7eb8f',
        icon: '●',
      },
      STARTING: {
        color: '#0958d9',
        bgColor: '#e6f4ff',
        borderColor: '#91caff',
        icon: '●',
      },
      DRAINING: {
        color: '#d46b08',
        bgColor: '#fff7e6',
        borderColor: '#ffd591',
        icon: '●',
      },
      STOPPED: {
        color: '#595959',
        bgColor: '#fafafa',
        borderColor: '#d9d9d9',
        icon: '■',
      },
      ERROR: {
        color: '#cf1322',
        bgColor: '#fff1f0',
        borderColor: '#ffa39e',
        icon: '●',
      },
      ACTIVE: {
        color: '#389e0d',
        bgColor: '#f6ffed',
        borderColor: '#b7eb8f',
        icon: '●',
      },
      INACTIVE: {
        color: '#8c8c8c',
        bgColor: '#fafafa',
        borderColor: '#d9d9d9',
        icon: '○',
      },
      ENABLED: {
        color: '#389e0d',
        bgColor: '#f6ffed',
        borderColor: '#b7eb8f',
        icon: '✓',
      },
      DISABLED: {
        color: '#8c8c8c',
        bgColor: '#fafafa',
        borderColor: '#d9d9d9',
        icon: '✕',
      },
    };

    return statusMap[status] || statusMap.STOPPED;
  };

  const config = getStatusConfig(status);
  const displayText = text || STATUS_LABELS[status] || status;

  return (
    <span
      className={`status-badge status-badge-${size} ${ANIMATED_STATUSES.has(status) ? 'status-animated' : ''}`}
      style={{
        color: config.color,
        backgroundColor: config.bgColor,
        borderColor: config.borderColor,
      }}
    >
      {config.icon && <span className="status-icon">{config.icon}</span>}
      {displayText}
    </span>
  );
};

export default StatusBadge;
