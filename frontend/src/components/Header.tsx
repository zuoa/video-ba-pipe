import React, { useEffect, useState } from 'react';
import { Link, useLocation, history, useIntl } from '@umijs/max';
import { Dropdown, message, Tooltip } from 'antd';
import { getSystemInfo, SystemInfo } from '@/services/api';
import { SYSTEM_NAME_EN, SYSTEM_NAME_ZH } from '@/constants/branding';
import { LOGIN_PATH, clearAuthStorage } from '@/utils/auth';
import LanguageSwitch from '@/components/LanguageSwitch';
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
  ScanOutlined,
  IdcardOutlined,
} from '@ant-design/icons';

const DEFAULT_COMPANY_NAME = '码全科技';
const FRONTEND_VERSION = process.env.UMI_APP_VERSION || '';

const knownVersion = (version?: string | null) => {
  const normalized = String(version || '').trim();
  return normalized && normalized !== 'unknown' ? normalized : '';
};

const Header: React.FC = () => {
  const intl = useIntl();
  const t = (id: string) => intl.formatMessage({ id });
  const location = useLocation();
  const userStr = localStorage.getItem('user');
  const user = userStr ? JSON.parse(userStr) : null;
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null);
  const [companyName, setCompanyName] = useState(DEFAULT_COMPANY_NAME);

  useEffect(() => {
    let mounted = true;

    const loadVersion = async () => {
      try {
        const response = await getSystemInfo();
        if (mounted) {
          setSystemInfo(response);
          if (response?.company_name) {
            setCompanyName(response.company_name);
          }
        }
      } catch (error) {
        // Keep the frontend build visible and mark the other versions as unknown.
      }
    };

    loadVersion();

    return () => {
      mounted = false;
    };
  }, []);

  const handleLogout = () => {
    clearAuthStorage();
    message.success(t('app.logoutSuccess'));
    history.push(LOGIN_PATH);
  };

  const userMenuItems = [
    {
      key: 'api-docs',
      icon: <ReadOutlined />,
      label: <Link to="/api-docs">{t('nav.apiDocs')}</Link>,
    },
    ...(user?.role === 'admin' ? [{
      key: 'system-settings',
      icon: <SettingOutlined />,
      label: <Link to="/system-settings">{t('nav.systemSettings')}</Link>,
    }] : []),
    ...(user?.role === 'admin' ? [{
      key: 'users',
      icon: <TeamOutlined />,
      label: <Link to="/users">{t('nav.users')}</Link>,
    }] : []),
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: t('nav.logout'),
      onClick: handleLogout,
    },
  ];

  const menuItems = [
    ...(user?.role === 'admin' ? [{
      key: 'face-galleries',
      icon: <ScanOutlined />,
      label: <Link to="/face-galleries">{t('nav.faceRecognition')}</Link>,
    }] : []),
    ...(user?.role === 'admin' ? [{
      key: 'models',
      icon: <FunctionOutlined />,
      label: <Link to="/models">{t('nav.models')}</Link>,
    }] : []),
    ...(user?.role === 'admin' ? [{
      key: 'reid-models',
      icon: <IdcardOutlined />,
      label: <Link to="/reid-models">{t('nav.personReid')}</Link>,
    }] : []),
    ...(user?.role === 'admin' ? [{
      key: 'scripts',
      icon: <CodeOutlined />,
      label: <Link to="/scripts">{t('nav.scripts')}</Link>,
    }] : []),
    {
      key: 'external-apis',
      icon: <FunctionOutlined />,
      label: <Link to="/external-apis">{t('nav.externalApis')}</Link>,
    },
    {
      key: 'algorithms',
      icon: <ExperimentOutlined />,
      label: <Link to="/algorithms">{t('nav.algorithms')}</Link>,
    },
  ];

  const isActive = (path: string) => {
    return location.pathname === path || location.pathname.startsWith(path + '/');
  };

  const isAlgorithmActive = location.pathname === '/models' ||
    location.pathname === '/face-galleries' ||
    location.pathname === '/reid-models' ||
    location.pathname === '/scripts' ||
    location.pathname === '/external-apis' ||
    location.pathname === '/algorithms';

  const frontendVersion = knownVersion(FRONTEND_VERSION);
  const apiVersion = knownVersion(
    systemInfo?.service_versions?.api?.version || systemInfo?.version,
  );
  const workerVersion = knownVersion(systemInfo?.service_versions?.worker?.version);
  const workerStatus = systemInfo?.service_versions?.worker?.status;
  const workerReady = workerStatus === 'ready';
  const comparableVersions = [frontendVersion, apiVersion, workerVersion].filter(Boolean);
  const versionsMismatch = new Set(comparableVersions).size > 1;
  const versionsComplete = Boolean(frontendVersion && apiVersion && workerVersion && workerReady);
  const versionState = versionsMismatch ? 'mismatch' : versionsComplete ? 'aligned' : 'unknown';
  const versionLabel = versionsMismatch
    ? t('version.mismatch')
    : apiVersion
      ? `v${apiVersion}`
      : t('version.unknown');
  const versionSummary = versionsMismatch
    ? t('version.mismatchDetail')
    : versionsComplete
      ? t('version.alignedDetail')
      : t('version.partialDetail');
  const versionDetails = (
    <div className="service-version-details">
      <div className={`service-version-details__summary service-version-details__summary--${versionState}`}>
        {versionSummary}
      </div>
      <div className="service-version-details__row">
        <span>{t('version.frontend')}</span>
        <code>{frontendVersion || t('version.notInjected')}</code>
      </div>
      <div className="service-version-details__row">
        <span>API / Control</span>
        <code>{apiVersion || t('version.unknownShort')}</code>
      </div>
      <div className="service-version-details__row">
        <span>{t('version.worker')}</span>
        <code>{workerVersion || (workerStatus === 'unavailable' ? t('version.unavailable') : t('version.unknown'))}</code>
      </div>
    </div>
  );

  return (
    <header className="site-header">
      <div className="site-header__inner">
        <Link
          to="/dashboard"
          className="site-brand"
          aria-label={t('app.title')}
        >
          <div className="site-brand__icon">
            <VideoCameraOutlined />
          </div>
          <div className="site-brand__text">
            <strong>{intl.locale === 'en-US' ? SYSTEM_NAME_EN : SYSTEM_NAME_ZH}</strong>
            <span>{t('app.tagline')}</span>
          </div>
          <span className="site-brand__company" title={companyName}>
            {intl.locale === 'en-US' && companyName === DEFAULT_COMPANY_NAME ? 'Maquan Technology' : companyName}
          </span>
          <Tooltip title={versionDetails} placement="bottomLeft">
            <span
              className={`site-brand__version site-brand__version--${versionState}`}
              aria-label={`${versionLabel}, ${versionSummary}`}
            >
              <span className="site-brand__version-dot" aria-hidden="true" />
              {versionLabel}
            </span>
          </Tooltip>
        </Link>

        <nav className="site-nav">
          <Link
            to="/dashboard"
            className={`nav-link ${isActive('/dashboard') ? 'active' : ''}`}
          >
            <HomeOutlined />
            <span>{t('nav.dashboard')}</span>
          </Link>

          <Link
            to="/video-sources"
            className={`nav-link ${isActive('/video-sources') ? 'active' : ''}`}
          >
            <VideoCameraOutlined />
            <span>{t('nav.videoSources')}</span>
          </Link>

          <Dropdown menu={{ items: menuItems }} placement="bottomLeft">
            <button
              type="button"
              aria-haspopup="menu"
              aria-label={t('nav.algorithmMenu')}
              className={`nav-link nav-link--button ${isAlgorithmActive ? 'active' : ''}`}
            >
              <ExperimentOutlined />
              <span>{t('nav.algorithms')}</span>
              <DownOutlined className="nav-link__arrow" />
            </button>
          </Dropdown>

          <Link
            to="/workflows"
            className={`nav-link ${isActive('/workflows') ? 'active' : ''}`}
          >
            <ApartmentOutlined />
            <span>{t('nav.workflows')}</span>
          </Link>

          <Link
            to="/alerts"
            className={`nav-link ${isActive('/alerts') ? 'active' : ''}`}
          >
            <BellOutlined />
            <span>{t('nav.alerts')}</span>
          </Link>
        </nav>

        <div className="site-tools">
          <LanguageSwitch className="site-language-switch" />
          <a
            href="/gpu-calculator"
            target="_blank"
            rel="noopener noreferrer"
            className="site-tools__button"
            title={t('nav.gpuCalculator')}
            aria-label={t('nav.gpuCalculator')}
          >
            <CalculatorOutlined />
          </a>

          <a
            href="/alert-wall"
            target="_blank"
            rel="noopener noreferrer"
            className="site-tools__button"
            title={t('nav.alertWall')}
            aria-label={t('nav.alertWall')}
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
                <span className="site-user__label">{t('nav.currentUser')}</span>
                <span className="site-user__name">{user?.username || t('nav.signedOut')}</span>
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
