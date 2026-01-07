import React from 'react';
import { Empty, Button } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';

interface EmptyStateProps {
  type?: 'alerts' | 'search';
  onRefresh?: () => void;
}

const EmptyState: React.FC<EmptyStateProps> = ({ type = 'alerts', onRefresh }) => {
  const config = {
    alerts: {
      description: '暂无告警记录',
      message: '系统运行正常，没有检测到告警信息',
    },
    search: {
      description: '未找到匹配的记录',
      message: '请尝试调整筛选条件',
    },
  };

  const currentConfig = config[type];

  return (
    <Empty
      description={
        <div>
          <div>{currentConfig.description}</div>
          <div style={{ color: '#8c8c8c', fontSize: 12, marginTop: 4 }}>
            {currentConfig.message}
          </div>
        </div>
      }
    >
      {onRefresh && (
        <Button type="primary" icon={<ReloadOutlined />} onClick={onRefresh}>
          刷新数据
        </Button>
      )}
    </Empty>
  );
};

export default EmptyState;
