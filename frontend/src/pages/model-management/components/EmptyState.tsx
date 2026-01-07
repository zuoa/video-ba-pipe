import React from 'react';
import { Empty, Button } from 'antd';
import { ApiOutlined, ReloadOutlined } from '@ant-design/icons';
import './EmptyState.css';

interface EmptyStateProps {
  hasFilter: boolean;
  onReset?: () => void;
}

const EmptyState: React.FC<EmptyStateProps> = ({ hasFilter, onReset }) => {
  if (hasFilter) {
    return (
      <div className="empty-state">
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <span>
              没有找到匹配的模型
              <br />
              <span style={{ fontSize: 12, color: '#8c8c8c' }}>请尝试调整筛选条件</span>
            </span>
          }
        >
          {onReset && (
            <Button icon={<ReloadOutlined />} onClick={onReset}>
              重置筛选
            </Button>
          )}
        </Empty>
      </div>
    );
  }

  return (
    <div className="empty-state">
      <Empty
        image={<ApiOutlined style={{ fontSize: 64, color: '#d9d9d9' }} />}
        description={
          <span>
            暂无模型
            <br />
            <span style={{ fontSize: 12, color: '#8c8c8c' }}>点击"上传模型"按钮添加第一个模型</span>
          </span>
        }
      />
    </div>
  );
};

export default EmptyState;
