import React from 'react';
import type { SemanticTone } from '../AppButton';
import './index.css';

const STATUS_LABELS: Record<string, string> = {
  STARTING: '启动中',
  RUNNING: '检测中',
  DRAINING: '排空中',
  STOPPED: '等待中',
  ERROR: '异常',
};

const ANIMATED_STATUSES = new Set(['STARTING', 'RUNNING', 'DRAINING', 'ERROR']);
const STATUS_TONES: Record<string, SemanticTone | 'muted'> = {
  RUNNING: 'success',
  STARTING: 'info',
  DRAINING: 'warning',
  STOPPED: 'muted',
  ERROR: 'danger',
  ACTIVE: 'success',
  INACTIVE: 'muted',
  ENABLED: 'success',
  DISABLED: 'muted',
};

export interface StatusBadgeProps {
  status: string;
  text?: string;
  size?: 'small' | 'default' | 'large';
  tone?: SemanticTone | 'muted';
  showIcon?: boolean;
}

const StatusBadge: React.FC<StatusBadgeProps> = ({
  status,
  text,
  size = 'default',
  tone,
  showIcon = true,
}) => {
  const displayText = text || STATUS_LABELS[status] || status;
  const resolvedTone = tone ?? STATUS_TONES[status] ?? 'muted';

  return (
    <span
      className={`status-badge status-badge-${size} status-badge--${resolvedTone} status-badge-${status.toLowerCase()} ${ANIMATED_STATUSES.has(status) ? 'status-animated' : ''}`}
    >
      {showIcon ? <span className="status-icon" aria-hidden="true" /> : null}
      {displayText}
    </span>
  );
};

export default StatusBadge;
