import React from 'react';
import './index.css';

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
  const displayText = text || status;

  return (
    <span
      className={`status-badge status-badge-${size} ${status === 'RUNNING' || status === 'ERROR' ? 'status-animated' : ''}`}
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
