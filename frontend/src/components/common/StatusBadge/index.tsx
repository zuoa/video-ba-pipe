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
    }> = {
      RUNNING: {
        color: '#389e0d',
        bgColor: '#f6ffed',
        borderColor: '#b7eb8f',
      },
      STARTING: {
        color: '#0958d9',
        bgColor: '#e6f4ff',
        borderColor: '#91caff',
      },
      DRAINING: {
        color: '#d46b08',
        bgColor: '#fff7e6',
        borderColor: '#ffd591',
      },
      STOPPED: {
        color: '#595959',
        bgColor: '#fafafa',
        borderColor: '#d9d9d9',
      },
      ERROR: {
        color: '#cf1322',
        bgColor: '#fff1f0',
        borderColor: '#ffa39e',
      },
      ACTIVE: {
        color: '#389e0d',
        bgColor: '#f6ffed',
        borderColor: '#b7eb8f',
      },
      INACTIVE: {
        color: '#8c8c8c',
        bgColor: '#fafafa',
        borderColor: '#d9d9d9',
      },
      ENABLED: {
        color: '#389e0d',
        bgColor: '#f6ffed',
        borderColor: '#b7eb8f',
      },
      DISABLED: {
        color: '#8c8c8c',
        bgColor: '#fafafa',
        borderColor: '#d9d9d9',
      },
    };

    return statusMap[status] || statusMap.STOPPED;
  };

  const config = getStatusConfig(status);
  const displayText = text || STATUS_LABELS[status] || status;

  return (
    <span
      className={`status-badge status-badge-${size} status-badge-${status.toLowerCase()} ${ANIMATED_STATUSES.has(status) ? 'status-animated' : ''}`}
      style={{
        color: config.color,
        backgroundColor: config.bgColor,
        borderColor: config.borderColor,
      }}
    >
      <span className="status-icon" aria-hidden="true" />
      {displayText}
    </span>
  );
};

export default StatusBadge;
