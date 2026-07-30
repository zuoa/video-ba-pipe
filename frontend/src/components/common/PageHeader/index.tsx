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
      <div className="page-header__left">
        <div className="page-header__icon">{icon}</div>
        <div className="page-header__content">
          <h3 className="page-header__title">{title}</h3>
          {subtitle && <p className="page-header__subtitle">{subtitle}</p>}
        </div>
      </div>
      <div className="page-header__right">
        <Space size={12} wrap>
          {count !== undefined && (
            <div className="page-header__count">
              <span className="page-header__count-number">{count}</span>
              <span className="page-header__count-label">{countLabel}</span>
            </div>
          )}
          {extra}
        </Space>
      </div>
    </div>
  );
};

export default PageHeader;
