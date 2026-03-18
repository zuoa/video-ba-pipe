import React from 'react';
import { VideoCameraOutlined } from '@ant-design/icons';
import './WelcomeBanner.css';

const WelcomeBanner: React.FC = () => {
  return (
    <div className="welcome-banner">
      <div className="welcome-content">
        <h2 className="welcome-title">系统概览</h2>
        <p className="welcome-subtitle">在一个界面内查看任务运行、算法配置与最新告警。</p>
      </div>
      <div className="welcome-icon-wrapper">
        <div className="welcome-icon-bg">
          <VideoCameraOutlined className="welcome-icon" />
        </div>
      </div>
    </div>
  );
};

export default WelcomeBanner;
