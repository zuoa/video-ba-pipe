import React from 'react';
import { VideoCameraOutlined } from '@ant-design/icons';
import './WelcomeBanner.css';

const WelcomeBanner: React.FC = () => {
  return (
    <div className="welcome-banner">
      <div className="welcome-content">
        <h2 className="welcome-title">欢迎使用视频分析系统</h2>
        <p className="welcome-subtitle">基于AI的智能视频监控与分析平台</p>
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
