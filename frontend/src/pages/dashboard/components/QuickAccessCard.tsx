import React from 'react';
import { RightOutlined } from '@ant-design/icons';
import './QuickAccessCard.css';

export interface QuickAccessItem {
  title: string;
  description: string;
  icon: React.ReactNode;
  iconColor?: string;
  iconBgColor?: string;
  path: string;
}

export interface QuickAccessCardProps {
  title: string;
  icon: React.ReactNode;
  items: QuickAccessItem[];
}

const QuickAccessCard: React.FC<QuickAccessCardProps> = ({ title, icon, items }) => {
  return (
    <div className="quick-access-card">
      <h3 className="quick-access-title">
        <span className="title-icon">{icon}</span>
        {title}
      </h3>
      <div className="quick-access-list">
        {items.map((item, index) => (
          <a key={index} href={item.path} className="quick-access-item">
            <div className="item-left">
              <div
                className="item-icon"
                style={{
                  background: item.iconBgColor || '#000000',
                }}
              >
                {item.icon}
              </div>
              <div className="item-content">
                <p className="item-title">{item.title}</p>
                <p className="item-description">{item.description}</p>
              </div>
            </div>
            <RightOutlined className="item-arrow" />
          </a>
        ))}
      </div>
    </div>
  );
};

export default QuickAccessCard;
