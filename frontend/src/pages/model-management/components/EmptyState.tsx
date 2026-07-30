import React from 'react';
import { ApiOutlined, ReloadOutlined } from '@ant-design/icons';
import AppButton from '@/components/common/AppButton';
import AppEmptyState from '@/components/common/AppEmptyState';
import './EmptyState.css';

interface EmptyStateProps {
  hasFilter: boolean;
  onReset?: () => void;
}

const EmptyState: React.FC<EmptyStateProps> = ({ hasFilter, onReset }) => {
  if (hasFilter) {
    return (
      <AppEmptyState
        className="empty-state"
        title="没有找到匹配的模型"
        description="请尝试调整筛选条件"
        action={onReset ? (
            <AppButton icon={<ReloadOutlined />} onClick={onReset}>
              重置筛选
            </AppButton>
        ) : undefined}
      />
    );
  }

  return (
    <AppEmptyState
      className="empty-state"
      image={<ApiOutlined className="empty-state__icon" />}
      title="暂无模型"
      description="点击“上传模型”按钮添加第一个模型"
    />
  );
};

export default EmptyState;
