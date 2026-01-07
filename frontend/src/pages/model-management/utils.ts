export const formatDate = (dateString: string): string => {
  if (!dateString) return '-';
  const date = new Date(dateString);
  const now = new Date();
  const diff = now.getTime() - date.getTime();
  const days = Math.floor(diff / (1000 * 60 * 60 * 24));

  if (days === 0) {
    const hours = Math.floor(diff / (1000 * 60 * 60));
    if (hours === 0) {
      const minutes = Math.floor(diff / (1000 * 60));
      return minutes === 0 ? '刚刚' : `${minutes}分钟前`;
    }
    return `${hours}小时前`;
  }
  
  if (days === 1) return '昨天';
  if (days < 7) return `${days}天前`;
  if (days < 30) return `${Math.floor(days / 7)}周前`;
  if (days < 365) return `${Math.floor(days / 30)}个月前`;
  
  return date.toLocaleDateString('zh-CN');
};

export const formatFileSize = (bytes: number): string => {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(2) + ' KB';
  if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
  return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
};

export const getModelTypeColor = (type: string): string => {
  const colors: Record<string, string> = {
    'YOLO': '#3b82f6',
    'ONNX': '#10b981',
    'TensorRT': '#f59e0b',
    'PyTorch': '#ef4444',
    'TFLite': '#8b5cf6',
    'Custom': '#6b7280',
  };
  return colors[type] || '#6b7280';
};

export const getFrameworkColor = (framework: string): string => {
  const colors: Record<string, string> = {
    'ultralytics': '#3b82f6',
    'onnx': '#10b981',
    'tensorrt': '#f59e0b',
    'pytorch': '#ef4444',
    'tflite': '#8b5cf6',
    'custom': '#6b7280',
  };
  return colors[framework] || '#6b7280';
};

