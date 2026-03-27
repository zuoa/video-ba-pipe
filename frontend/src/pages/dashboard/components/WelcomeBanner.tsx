import React from 'react';
import { VideoCameraOutlined } from '@ant-design/icons';
import './WelcomeBanner.css';

const WelcomeBanner: React.FC = () => {
  return (
    <div className="welcome-banner">
      <div className="welcome-content">
        <h2 className="welcome-title">控制台概览</h2>
        <p className="welcome-subtitle">视频源状态、算法配置和最新告警都汇总在这里，当前运行情况一眼就能看清。</p>
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
