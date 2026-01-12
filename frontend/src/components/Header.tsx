import React from 'react';
import { Link, useLocation, history } from '@umijs/max';
import { Dropdown, Button, message } from 'antd';
import './Header.css';
import {
  VideoCameraOutlined,
  HomeOutlined,
  BellOutlined,
  UserOutlined,
  CodeOutlined,
  ApartmentOutlined,
  ExperimentOutlined,
  DesktopOutlined,
  FunctionOutlined,
  DownOutlined,
  CalculatorOutlined,
  LogoutOutlined,
  TeamOutlined,
} from '@ant-design/icons';

const Header: React.FC = () => {
  const location = useLocation();
  const userStr = localStorage.getItem('user');
  const user = userStr ? JSON.parse(userStr) : null;

  const handleLogout = () => {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    message.success('退出成功');
    history.push('/login');
  };

  const userMenuItems = [
    ...(user?.role === 'admin' ? [{
      key: 'users',
      icon: <TeamOutlined />,
      label: <Link to="/users">用户管理</Link>,
    }] : []),
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: '退出登录',
      onClick: handleLogout,
    },
  ];

  const menuItems = [
    {
      key: 'models',
      icon: <FunctionOutlined />,
      label: <Link to="/models">模型管理</Link>,
    },
    {
      key: 'scripts',
      icon: <CodeOutlined />,
      label: <Link to="/scripts">脚本管理</Link>,
    },
    {
      key: 'algorithms',
      icon: <ExperimentOutlined />,
      label: <Link to="/algorithms">算法管理</Link>,
    },
  ];

  const navItems = [
    { path: '/dashboard', icon: <HomeOutlined />, label: '仪表盘' },
    { path: '/video-sources', icon: <VideoCameraOutlined />, label: '视频源' },
    { path: '/workflows', icon: <ApartmentOutlined />, label: '算法编排' },
    { path: '/alerts', icon: <BellOutlined />, label: '告警记录' },
  ];

  const isActive = (path: string) => {
    return location.pathname === path || location.pathname.startsWith(path + '/');
  };

  const isAlgorithmActive = location.pathname === '/models' ||
    location.pathname === '/scripts' ||
    location.pathname === '/algorithms';

  return (
    <div style={{
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
      height: '64px',
      padding: '0 24px',
      background: '#fff',
      borderBottom: '1px solid #e5e7eb'
    }}>
      {/* Logo */}
      <div className="logo-container">
        <div className="logo-icon">
          <VideoCameraOutlined style={{ fontSize: '18px' }} />
        </div>
        <div className="logo-text">
          <h1>视频行为分析系统</h1>
          <p>Video Behavior Analysis</p>
        </div>
      </div>

      {/* 导航菜单 */}
      <div className="nav-menu">
        {/* 仪表盘 */}
        <Link
          to="/dashboard"
          className={`nav-link ${isActive('/dashboard') ? 'active' : ''}`}
        >
          <HomeOutlined style={{ marginRight: '8px' }} />
          仪表盘
        </Link>

        {/* 视频源 */}
        <Link
          to="/video-sources"
          className={`nav-link ${isActive('/video-sources') ? 'active' : ''}`}
        >
          <VideoCameraOutlined style={{ marginRight: '8px' }} />
          视频源
        </Link>

        {/* 算法管理下拉菜单 */}
        <Dropdown menu={{ items: menuItems }} placement="bottomLeft">
          <Link
            to="#"
            onClick={(e) => e.preventDefault()}
            className={`nav-link ${isAlgorithmActive ? 'active' : ''}`}
          >
            <ExperimentOutlined style={{ marginRight: '8px' }} />
            算法管理
            <DownOutlined style={{ marginLeft: '4px', fontSize: '12px' }} />
          </Link>
        </Dropdown>

        {/* 算法编排 */}
        <Link
          to="/workflows"
          className={`nav-link ${isActive('/workflows') ? 'active' : ''}`}
        >
          <ApartmentOutlined style={{ marginRight: '8px' }} />
          算法编排
        </Link>

        {/* 告警记录 */}
        <Link
          to="/alerts"
          className={`nav-link ${isActive('/alerts') ? 'active' : ''}`}
        >
          <BellOutlined style={{ marginRight: '8px' }} />
          告警记录
        </Link>
      </div>

      {/* 右侧操作 */}
      <div className="header-actions">
        <Button
          type="text"
          icon={<BellOutlined />}
          style={{ position: 'relative' }}
        >
          <span className="notification-badge"></span>
        </Button>

        <Button
          type="text"
          icon={<CalculatorOutlined />}
          href="/gpu-calculator"
          target="_blank"
          title="GPU需求计算器"
        />

        <Button
          type="text"
          icon={<DesktopOutlined />}
          href="/alert-wall"
          target="_blank"
          title="大屏"
        />

        <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
          <div className="user-avatar-container">
            <div className="user-avatar">
              <UserOutlined style={{ fontSize: '12px' }} />
            </div>
            <span className="user-username">
              {user?.username || '未登录'}
            </span>
          </div>
        </Dropdown>
      </div>
    </div>
  );
};

export default Header;
