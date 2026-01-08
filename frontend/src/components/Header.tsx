import React from 'react';
import { Link, useLocation, history } from '@umijs/max';
import { Dropdown, Button, message } from 'antd';
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
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        <div style={{
          width: '40px',
          height: '40px',
          background: '#000',
          borderRadius: '8px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#fff',
          boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)'
        }}>
          <VideoCameraOutlined style={{ fontSize: '18px' }} />
        </div>
        <div>
          <h1 style={{ fontSize: '18px', fontWeight: 'bold', color: '#111827', margin: 0, lineHeight: 1.2 }}>
            视频行为分析系统
          </h1>
          <p style={{ fontSize: '12px', color: '#6b7280', margin: 0, lineHeight: 1.2 }}>
            Video Behavior Analysis
          </p>
        </div>
      </div>

      {/* 导航菜单 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
        {navItems.map((item) => (
          <Link
            key={item.path}
            to={item.path}
            style={{
              padding: '8px 16px',
              fontSize: '14px',
              fontWeight: '500',
              color: isActive(item.path) ? '#000' : '#374151',
              textDecoration: 'none',
              position: 'relative',
              transition: 'all 0.2s ease',
              borderRadius: '4px',
            }}
            onMouseEnter={(e) => {
              if (!isActive(item.path)) {
                e.currentTarget.style.background = '#f3f4f6';
              }
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent';
            }}
          >
            {React.cloneElement(item.icon, { style: { marginRight: '8px' } })}
            {item.label}
            {isActive(item.path) && (
              <div style={{
                position: 'absolute',
                bottom: '0',
                left: '0',
                right: '0',
                height: '3px',
                background: '#000',
              }}></div>
            )}
          </Link>
        ))}

        {/* 算法管理下拉菜单 */}
        <Dropdown menu={{ items: menuItems }} placement="bottomLeft">
          <Link
            to="#"
            onClick={(e) => e.preventDefault()}
            style={{
              padding: '8px 16px',
              fontSize: '14px',
              fontWeight: '500',
              color: isAlgorithmActive ? '#000' : '#374151',
              textDecoration: 'none',
              position: 'relative',
              transition: 'all 0.2s ease',
              borderRadius: '4px',
            }}
            onMouseEnter={(e) => {
              if (!isAlgorithmActive) {
                e.currentTarget.style.background = '#f3f4f6';
              }
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent';
            }}
          >
            <ExperimentOutlined style={{ marginRight: '8px' }} />
            算法管理
            <DownOutlined style={{ marginLeft: '4px', fontSize: '12px' }} />
          </Link>
        </Dropdown>
      </div>

      {/* 右侧操作 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        <Button
          type="text"
          icon={<BellOutlined />}
          style={{ position: 'relative' }}
        >
          <span style={{
            position: 'absolute',
            top: '8px',
            right: '8px',
            width: '8px',
            height: '8px',
            background: '#ef4444',
            borderRadius: '50%',
          }}></span>
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
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            paddingLeft: '12px',
            borderLeft: '1px solid #e5e7eb',
            cursor: 'pointer',
          }}>
            <div style={{
              width: '32px',
              height: '32px',
              background: '#000',
              borderRadius: '50%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#fff',
              fontSize: '12px',
              fontWeight: '600',
            }}>
              <UserOutlined style={{ fontSize: '12px' }} />
            </div>
            <span style={{
              fontSize: '14px',
              fontWeight: '500',
              color: '#374151',
              display: 'block',
            }}>
              {user?.username || '未登录'}
            </span>
          </div>
        </Dropdown>
      </div>
    </div>
  );
};

export default Header;
