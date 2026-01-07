import React from 'react';
import { Tag } from 'antd';

// 告警类型颜色映射
const ALERT_COLORS: Record<string, string> = {
  warning: 'orange',
  error: 'red',
  info: 'blue',
  critical: 'purple',
  person_detection: 'blue',
  phone_detection_2stage: 'orange',
};

// 告警类型标签映射
const ALERT_LABELS: Record<string, string> = {
  warning: '警告',
  error: '错误',
  info: '信息',
  critical: '严重',
  person_detection: '人员检测',
  phone_detection_2stage: '手机检测',
};

interface AlertTypeBadgeProps {
  type: string;
  showIcon?: boolean;
}

const AlertTypeBadge: React.FC<AlertTypeBadgeProps> = ({ type, showIcon = true }) => {
  const color = ALERT_COLORS[type] || 'blue';
  const label = ALERT_LABELS[type] || type;

  return <Tag color={color}>{label}</Tag>;
};

export default AlertTypeBadge;
