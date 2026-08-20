import React, { useEffect, useState } from 'react';
import { Link, useLocation, history } from '@umijs/max';
import { Dropdown, message } from 'antd';
import { getSystemInfo } from '@/services/api';
import { SYSTEM_NAME_EN, SYSTEM_NAME_ZH } from '@/constants/branding';
import { LOGIN_PATH, clearAuthStorage } from '@/utils/auth';
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
  SettingOutlined,
  ReadOutlined,
} from '@ant-design/icons';

const DEFAULT_COMPANY_NAME = '码全科技';

const Header: React.FC = () => {
  const location = useLocation();
  const userStr = localStorage.getItem('user');
  const user = userStr ? JSON.parse(userStr) : null;
  const [appVersion, setAppVersion] = useState<string>('');
  const [companyName, setCompanyName] = useState(DEFAULT_COMPANY_NAME);

  useEffect(() => {
    let mounted = true;

    const loadVersion = async () => {
      try {
        const response = await getSystemInfo();
        if (mounted && response?.version) {
          setAppVersion(response.version);
        }
        if (mounted && response?.company_name) {
          setCompanyName(response.company_name);
        }
      } catch (error) {
        // Silently keep the version tag hidden when the endpoint is unavailable.
      }
    };

    loadVersion();

    return () => {
      mounted = false;
    };
  }, []);

  const handleLogout = () => {
    clearAuthStorage();
    message.success('退出成功');
    history.push(LOGIN_PATH);
  };

  const userMenuItems = [
    {
      key: 'api-docs',
      icon: <ReadOutlined />,
      label: <Link to="/api-docs">API 文档</Link>,
    },
    ...(user?.role === 'admin' ? [{
      key: 'system-settings',
      icon: <SettingOutlined />,
      label: <Link to="/system-settings">系统设置</Link>,
    }] : []),
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
    ...(user?.role === 'admin' ? [{
      key: 'models',
      icon: <FunctionOutlined />,
      label: <Link to="/models">模型管理</Link>,
    }] : []),
    ...(user?.role === 'admin' ? [{
      key: 'scripts',
      icon: <CodeOutlined />,
      label: <Link to="/scripts">脚本管理</Link>,
    }] : []),
    {
      key: 'external-apis',
      icon: <FunctionOutlined />,
      label: <Link to="/external-apis">外部 API</Link>,
    },
    {
      key: 'algorithms',
      icon: <ExperimentOutlined />,
      label: <Link to="/algorithms">算法管理</Link>,
    },
  ];

  const isActive = (path: string) => {
    return location.pathname === path || location.pathname.startsWith(path + '/');
  };

  const isAlgorithmActive = location.pathname === '/models' ||
    location.pathname === '/scripts' ||
    location.pathname === '/external-apis' ||
    location.pathname === '/algorithms';

  return (
    <header className="site-header">
      <div className="site-header__inner">
        <Link
          to="/dashboard"
          className="site-brand"
        >
          <div className="site-brand__icon">
            <VideoCameraOutlined />
          </div>
          <div className="site-brand__text">
            <strong>{SYSTEM_NAME_ZH}</strong>
            <span>{SYSTEM_NAME_EN}</span>
          </div>
          <span className="site-brand__company" title={companyName}>{companyName}</span>
          {appVersion ? <span className="site-brand__version">v{appVersion}</span> : null}
        </Link>

        <nav className="site-nav">
          <Link
            to="/dashboard"
            className={`nav-link ${isActive('/dashboard') ? 'active' : ''}`}
          >
            <HomeOutlined />
            <span>仪表盘</span>
          </Link>

          <Link
            to="/video-sources"
            className={`nav-link ${isActive('/video-sources') ? 'active' : ''}`}
          >
            <VideoCameraOutlined />
            <span>视频源</span>
          </Link>

          <Dropdown menu={{ items: menuItems }} placement="bottomLeft">
            <button
              type="button"
              aria-haspopup="menu"
              aria-label="打开算法管理菜单"
              className={`nav-link nav-link--button ${isAlgorithmActive ? 'active' : ''}`}
            >
              <ExperimentOutlined />
              <span>算法管理</span>
              <DownOutlined className="nav-link__arrow" />
            </button>
          </Dropdown>

          <Link
            to="/workflows"
            className={`nav-link ${isActive('/workflows') ? 'active' : ''}`}
          >
            <ApartmentOutlined />
            <span>算法编排</span>
          </Link>

          <Link
            to="/alerts"
            className={`nav-link ${isActive('/alerts') ? 'active' : ''}`}
          >
            <BellOutlined />
            <span>告警记录</span>
          </Link>
        </nav>

        <div className="site-tools">
          <a
            href="/gpu-calculator"
            target="_blank"
            rel="noopener noreferrer"
            className="site-tools__button"
            title="算力"
            aria-label="算力"
          >
            <CalculatorOutlined />
          </a>

          <a
            href="/alert-wall"
            target="_blank"
            rel="noopener noreferrer"
            className="site-tools__button"
            title="大屏"
            aria-label="大屏"
          >
            <DesktopOutlined />
          </a>

          <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
            <Link
              to="#"
              onClick={(e) => e.preventDefault()}
              className="site-user"
            >
              <span className="site-user__avatar">
                <UserOutlined />
              </span>
              <span className="site-user__meta">
                <span className="site-user__label">当前用户</span>
                <span className="site-user__name">{user?.username || '未登录'}</span>
              </span>
              <DownOutlined className="site-user__arrow" />
            </Link>
          </Dropdown>
        </div>
      </div>
    </header>
  );
};

export default Header;
