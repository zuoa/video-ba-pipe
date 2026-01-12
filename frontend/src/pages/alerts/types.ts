// 告警类型定义
export interface Alert {
  id: number;
  task_id: number;
  source_id?: number;
  workflow_id?: number;
  workflow_name?: string;
  alert_type: string;
  alert_message: string;
  alert_time: string;
  detection_count: number;
  alert_image?: string;
  alert_image_ori?: string;
  alert_video?: string;
  detection_images?: string | DetectionImage[];
  window_stats?: string | WindowStats;
}

export interface Workflow {
  id: number;
  name: string;
  description?: string;
  enabled: boolean;
}

export interface DetectionImage {
  image_path: string;
  detection_time?: string;
  timestamp?: number;
}

export interface WindowStats {
  detection_count: number;
  total_count: number;
  detection_ratio: number;
  max_consecutive: number;
}

export interface Task {
  id: number;
  name: string;
  source_code: string;
  status: string;
}

export interface AlertsResponse {
  data: Alert[];
  pagination: {
    page: number;
    per_page: number;
    total: number;
    total_pages: number;
  };
}

export interface AlertFilter {
  task_id?: string;
  workflow_id?: string;
  alert_type?: string;
  time_range?: string;
  start_time?: string;
  end_time?: string;
  page?: number;
  per_page?: number;
}

// 告警类型颜色配置
export const ALERT_TYPE_CONFIG: Record<string, {
  label: string;
  color: string;
  bgColor: string;
  borderColor: string;
  icon: string;
  gradient: string;
}> = {
  warning: {
    label: '警告',
    color: '#faad14',
    bgColor: '#fff7e6',
    borderColor: '#ffd591',
    icon: 'WarningOutlined',
    gradient: 'from-yellow-400 to-orange-500',
  },
  error: {
    label: '错误',
    color: '#ff4d4f',
    bgColor: '#fff1f0',
    borderColor: '#ffccc7',
    icon: 'CloseCircleOutlined',
    gradient: 'from-red-400 to-pink-500',
  },
  info: {
    label: '信息',
    color: '#1890ff',
    bgColor: '#e6f7ff',
    borderColor: '#91d5ff',
    icon: 'InfoCircleOutlined',
    gradient: 'from-blue-400 to-cyan-500',
  },
  critical: {
    label: '严重',
    color: '#722ed1',
    bgColor: '#f9f0ff',
    borderColor: '#d3adf7',
    icon: 'ExclamationCircleOutlined',
    gradient: 'from-purple-500 to-pink-500',
  },
  person_detection: {
    label: '人员检测',
    color: '#1890ff',
    bgColor: '#e6f7ff',
    borderColor: '#91d5ff',
    icon: 'UserOutlined',
    gradient: 'from-blue-400 to-cyan-500',
  },
  phone_detection_2stage: {
    label: '手机检测',
    color: '#faad14',
    bgColor: '#fff7e6',
    borderColor: '#ffd591',
    icon: 'MobileOutlined',
    gradient: 'from-yellow-400 to-orange-500',
  },
};

export const getAlertTypeConfig = (type: string) => {
  const lowerType = type.toLowerCase();
  return ALERT_TYPE_CONFIG[lowerType] || ALERT_TYPE_CONFIG.info;
};
