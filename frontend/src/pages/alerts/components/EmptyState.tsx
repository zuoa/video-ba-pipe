import { tr } from '@/i18n/tr';
import React from 'react';
import { ReloadOutlined } from '@ant-design/icons';
import AppButton from '@/components/common/AppButton';
import AppEmptyState from '@/components/common/AppEmptyState';

interface EmptyStateProps {
  type?: 'alerts' | 'search' | 'testResults';
  onRefresh?: () => void;
}

const EmptyState: React.FC<EmptyStateProps> = ({ type = 'alerts', onRefresh }) => {
  const config = {
    alerts: {
      description: tr("暂无告警记录"),
      message: tr("系统运行正常，没有检测到告警信息"),
    },
    search: {
      description: tr("未找到匹配的记录"),
      message: tr("请尝试调整筛选条件"),
    },
    testResults: {
      description: tr('暂无编排测试结果'),
      message: tr('运行一次编排测试后，结果会显示在这里。'),
    },
  };

  const currentConfig = config[type];

  return (
    <AppEmptyState
      title={currentConfig.description}
      description={currentConfig.message}
      action={onRefresh ? (
        <AppButton variant="solid" icon={<ReloadOutlined />} onClick={onRefresh}>
          {tr("刷新数据")}
        </AppButton>
      ) : undefined}
    />
  );
};

export default EmptyState;
