import React from 'react';
import { Space } from 'antd';
import './index.css';

export interface PageHeaderProps {
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
  extra?: React.ReactNode;
  count?: number;
  countLabel?: string;
}

const PageHeader: React.FC<PageHeaderProps> = ({
  icon,
  title,
  subtitle,
  extra,
  count,
  countLabel = '总数',
}) => {
  return (
    <div className="page-header">
      <div className="header-left">
        <div className="header-icon">{icon}</div>
        <div className="header-content">
          <h3 className="page-title">{title}</h3>
          {subtitle && <p className="page-subtitle">{subtitle}</p>}
        </div>
      </div>
      <div className="header-right">
        <Space size="middle">
          {count !== undefined && (
            <div className="header-count">
              <span className="count-number">{count}</span>
              <span className="count-label">{countLabel}</span>
            </div>
          )}
          {extra}
        </Space>
      </div>
    </div>
  );
};

export default PageHeader;
