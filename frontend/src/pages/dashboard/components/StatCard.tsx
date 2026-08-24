import React from 'react';
import './StatCard.css';

export interface StatCardProps {
  icon: React.ReactNode;
  title: string;
  value: number | string;
  subtitle?: string;
  footer?: React.ReactNode;
  iconBgColor?: string;
  onClick?: () => void;
}

const StatCard: React.FC<StatCardProps> = ({
  icon,
  title,
  value,
  subtitle,
  footer,
  iconBgColor = '#000000',
  onClick,
}) => {
  return (
    <div
      className={`dashboard-stat-card${onClick ? ' is-interactive' : ''}`}
      onClick={onClick}
    >
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
          {subtitle ? <p className="stat-card-subtitle">{subtitle}</p> : null}
        </div>
      </div>
      {footer ? <div className="stat-card-footer">{footer}</div> : null}
    </div>
  );
};

export default StatCard;
