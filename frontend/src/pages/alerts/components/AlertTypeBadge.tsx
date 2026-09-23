import { tr } from '@/i18n/tr';
import React from 'react';
import StatusBadge from '@/components/common/StatusBadge';
import type { SemanticTone } from '@/components/common/AppButton';

const ALERT_TONES: Record<string, SemanticTone> = {
  warning: 'warning',
  error: 'danger',
  info: 'info',
  critical: 'danger',
  person_detection: 'info',
  phone_detection_2stage: 'warning',
};

// 告警类型标签映射
const ALERT_LABELS: Record<string, string> = {
  warning: tr("警告"),
  error: tr("错误"),
  info: tr("信息"),
  critical: tr("严重"),
  person_detection: tr("人员检测"),
  phone_detection_2stage: tr("手机检测"),
};

interface AlertTypeBadgeProps {
  type: string;
  showIcon?: boolean;
}

const AlertTypeBadge: React.FC<AlertTypeBadgeProps> = ({ type, showIcon = true }) => {
  const label = ALERT_LABELS[type] || type;

  return (
    <StatusBadge
      status={type}
      text={label}
      tone={ALERT_TONES[type] || 'info'}
      size="small"
      showIcon={showIcon}
    />
  );
};

export default AlertTypeBadge;
