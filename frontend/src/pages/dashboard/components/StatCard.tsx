import React from 'react';
import { Badge } from 'antd';
import './StatCard.css';

export interface StatCardProps {
  icon: React.ReactNode;
  title: string;
  value: number | string;
  subtitle?: string;
  iconColor?: string;
  iconBgColor?: string;
  trendIcon?: React.ReactNode;
  onClick?: () => void;
}

const StatCard: React.FC<StatCardProps> = ({
  icon,
  title,
  value,
  subtitle,
  iconColor = '#000000',
  iconBgColor = '#000000',
  trendIcon,
  onClick,
}) => {
  return (
    <div className="dashboard-stat-card" onClick={onClick}>
      <div className="stat-card-header">
        <div
          className="stat-card-icon"
          style={{
            background: `linear-gradient(135deg, ${iconBgColor} 0%, ${iconBgColor}dd 100%)`,
          }}
        >
          {icon}
        </div>
        <span className="stat-card-label">{title}</span>
      </div>
      <div className="stat-card-body">
        <div className="stat-card-info">
          <p className="stat-card-value">{value}</p>
          {subtitle && <p className="stat-card-subtitle">{subtitle}</p>}
        </div>
        {trendIcon && <div className="stat-card-trend">{trendIcon}</div>}
      </div>
    </div>
  );
};

export default StatCard;
