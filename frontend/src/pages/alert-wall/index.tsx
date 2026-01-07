import React, { useState, useEffect, useCallback, useRef } from 'react';
import { getAlerts, getTodayAlertsCount, getVideoSources } from '@/services/api';
import { Alert, Task } from '../alerts/types';
import AlertTypeBadge from '../alerts/components/AlertTypeBadge';
import RelativeTime from '../alerts/components/RelativeTime';
import {
  SafetyOutlined,
  ExclamationCircleOutlined,
  VideoCameraOutlined,
  ClockCircleOutlined,
  CheckCircleOutlined,
  AppstoreOutlined,
  WarningOutlined,
  CloseCircleOutlined,
  InfoCircleOutlined,
  UserOutlined,
  MobileOutlined,
  SyncOutlined,
  DesktopOutlined,
  FireOutlined,
} from '@ant-design/icons';
import './index.css';

const AlertWallPage: React.FC = () => {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [mainAlert, setMainAlert] = useState<Alert | null>(null);
  const [todayCount, setTodayCount] = useState(0);
  const [totalCount, setTotalCount] = useState(0);
  const [currentTime, setCurrentTime] = useState(new Date());
  const [activeTasks, setActiveTasks] = useState(0);
  const [isNewAlert, setIsNewAlert] = useState(false);
  const [imageError, setImageError] = useState(false);
  const mainDisplayRef = useRef<HTMLDivElement>(null);
  const logoRef = useRef<HTMLDivElement>(null);

  // 加载数据
  const loadData = useCallback(async () => {
    try {
      const [alertsResponse, todayCountResponse, tasksResponse] = await Promise.all([
        getAlerts({ page: 1, per_page: 50 }),
        getTodayAlertsCount(),
        getVideoSources(),
      ]);

      const newAlerts = alertsResponse.data || [];
      const previousAlertId = mainAlert?.id;

      setAlerts(newAlerts);
      setTotalCount(alertsResponse.pagination?.total || 0);

      // 检查今日告警数量是否变化
      const newTodayCount = todayCountResponse.count || 0;
      if (newTodayCount !== todayCount) {
        setTodayCount(newTodayCount);
      }

      setTasks(tasksResponse || []);
      setActiveTasks(tasksResponse?.filter((t: Task) => t.status === 'RUNNING').length || 0);

      // 检查是否有新告警
      if (newAlerts.length > 0 && previousAlertId && newAlerts[0].id !== previousAlertId) {
        setIsNewAlert(true);
        setMainAlert(newAlerts[0]);

        // 触发闪烁边框动画
        if (mainDisplayRef.current) {
          mainDisplayRef.current.classList.remove('flash-border');
          void mainDisplayRef.current.offsetWidth; // 触发重绘
          mainDisplayRef.current.classList.add('flash-border');
        }

        // 触发图标跳动动画
        if (logoRef.current) {
          logoRef.current.classList.remove('alert-icon-bounce');
          void logoRef.current.offsetWidth;
          logoRef.current.classList.add('alert-icon-bounce');
        }

        // 1秒后重置新告警标志
        setTimeout(() => setIsNewAlert(false), 1000);
      } else if (!mainAlert && newAlerts.length > 0) {
        setMainAlert(newAlerts[0]);
      }

      // 重置图片错误状态
      setImageError(false);
    } catch (error) {
      console.error('加载数据失败:', error);
    }
  }, [mainAlert, todayCount]);

  // 更新时间
  const updateTime = useCallback(() => {
    setCurrentTime(new Date());
  }, []);

  // 初始化
  useEffect(() => {
    loadData();
    updateTime();

    // 定时更新
    const timeInterval = setInterval(updateTime, 1000);
    const dataInterval = setInterval(loadData, 5000);

    return () => {
      clearInterval(timeInterval);
      clearInterval(dataInterval);
    };
  }, [loadData, updateTime]);

  // 选择告警
  const selectAlert = (index: number) => {
    if (index >= 0 && index < alerts.length) {
      setMainAlert(alerts[index]);
      setImageError(false);
    }
  };

  // 处理图片加载错误
  const handleImageError = () => {
    setImageError(true);
  };

  const mainTask = mainAlert ? tasks.find(t => t.id === mainAlert.task_id) : null;

  // 获取告警类型图标
  const getAlertTypeIcon = (type: string): React.ReactNode => {
    const iconMap: Record<string, React.ReactNode> = {
      warning: <WarningOutlined />,
      error: <CloseCircleOutlined />,
      info: <InfoCircleOutlined />,
      critical: <ExclamationCircleOutlined />,
      person_detection: <UserOutlined />,
      phone_detection_2stage: <MobileOutlined />,
    };
    return iconMap[type.toLowerCase()] || <InfoCircleOutlined />;
  };

  return (
    <div className="alert-wall">
      {/* 背景效果 */}
      <div className="grid-bg" />
      <div className="scan-line" />
      <div className="particles-container">
        {/* 粒子将由 JavaScript 生成 */}
      </div>

      {/* 主容器 */}
      <div className="alert-wall-container">
        {/* 顶部标题栏 */}
        <header className="wall-header">
          <div className="header-left">
            <div className="header-logo" ref={logoRef}>
              <SafetyOutlined />
            </div>
            <div>
              <h1 className="header-title">智能监控告警中心</h1>
              <p className="header-subtitle">Intelligent Monitoring Alert Center</p>
            </div>
          </div>

          <div className="header-stats">
            <div className="stat-item">
              <div className="stat-icon-wrapper today-icon">
                <FireOutlined />
              </div>
              <div className="stat-content">
                <div className="stat-value digital-font" id="todayCount">{todayCount}</div>
                <div className="stat-label">今日告警</div>
              </div>
            </div>
            <div className="stat-item">
              <div className="stat-icon-wrapper total-icon">
                <AppstoreOutlined />
              </div>
              <div className="stat-content">
                <div className="stat-value digital-font" id="totalCount">{totalCount}</div>
                <div className="stat-label">总计告警</div>
              </div>
            </div>
            <div className="stat-item">
              <div className="stat-icon-wrapper active-icon">
                <VideoCameraOutlined />
              </div>
              <div className="stat-content">
                <div className="stat-value digital-font" id="activeTasks">{activeTasks}</div>
                <div className="stat-label">监控任务</div>
              </div>
            </div>
          </div>

          <div className="header-right">
            <div className="live-badge">
              <div className="status-dot" />
              <span>LIVE</span>
            </div>
            <div className="time-display">
              <div className="time-value digital-font">
                {currentTime.toLocaleTimeString('zh-CN', { hour12: false })}
              </div>
              <div className="date-value">
                {currentTime.toLocaleDateString('zh-CN', {
                  year: 'numeric',
                  month: '2-digit',
                  day: '2-digit',
                  weekday: 'short',
                })}
              </div>
            </div>
          </div>
        </header>

        {/* 主内容区 */}
        <div className="wall-content">
          {/* 左侧大幅画面 */}
          <div className="main-display" ref={mainDisplayRef}>
            <div className="corner-decoration top-left" />
            <div className="corner-decoration top-right" />
            <div className="corner-decoration bottom-left" />
            <div className="corner-decoration bottom-right" />

            {/* 顶部信息栏 */}
            <div className="main-display-header">
              <div className="main-display-title">
                <ExclamationCircleOutlined />
                <span>最新告警</span>
              </div>
              <div className="main-display-info">
                <div className="info-item">
                  <VideoCameraOutlined />
                  <span>{mainTask?.name || '--'}</span>
                </div>
                <div className="info-item">
                  <ClockCircleOutlined />
                  <span>
                    {mainAlert ? new Date(mainAlert.alert_time).toLocaleString('zh-CN', {
                      month: '2-digit',
                      day: '2-digit',
                      hour: '2-digit',
                      minute: '2-digit',
                      second: '2-digit',
                    }) : '--'}
                  </span>
                </div>
              </div>
            </div>

            {/* 图片展示区 */}
            <div className="main-display-image">
              {mainAlert ? (
                mainAlert.alert_image && !imageError ? (
                  <img
                    key={mainAlert.id}
                    src={`/api/image/frames/${mainAlert.alert_image}`}
                    alt="Alert"
                    className={`main-image ${isNewAlert ? 'alert-switch-animation' : ''}`}
                    onError={handleImageError}
                  />
                ) : (
                  <div className={`no-alert-placeholder ${isNewAlert ? 'alert-switch-animation' : ''}`}>
                    <div className="placeholder-icon">
                      {getAlertTypeIcon(mainAlert.alert_type)}
                    </div>
                    <p>{mainAlert.alert_type}</p>
                  </div>
                )
              ) : (
                <div className="no-alert">
                  <CheckCircleOutlined />
                  <p>系统运行正常</p>
                  <p>暂无告警信息</p>
                </div>
              )}
            </div>

            {/* 底部信息 */}
            <div className="main-display-footer">
              <div className="footer-left">
                {mainAlert && <AlertTypeBadge type={mainAlert.alert_type} showIcon />}
                {mainAlert && mainAlert.detection_count > 1 && (
                  <span className="detection-count">
                    检测 {mainAlert.detection_count} 次
                  </span>
                )}
              </div>
              <div className="footer-message">
                {mainAlert ? (mainAlert.alert_message || '无详细信息') : '等待告警数据...'}
              </div>
            </div>
          </div>

          {/* 右侧滚动列表 */}
          <div className="alert-list-panel">
            <div className="list-header">
              <div className="list-title">
                <AppstoreOutlined />
                <span>实时告警列表</span>
              </div>
              <div className="list-count">
                最近 <span className="count-number">50</span> 条
              </div>
            </div>

            {/* 告警列表 */}
            <div className="alert-list">
              {alerts.length === 0 ? (
                <div className="list-empty">
                  <CheckCircleOutlined />
                  <p>暂无告警记录</p>
                </div>
              ) : (
                alerts.map((alert, index) => {
                  const task = tasks.find(t => t.id === alert.task_id);
                  return (
                    <div
                      key={alert.id}
                      className={`alert-list-item ${index === 0 ? 'latest' : ''}`}
                      onClick={() => selectAlert(index)}
                    >
                      <div className="alert-item-content">
                        {alert.alert_image ? (
                          <img
                            src={`/api/image/frames/${alert.alert_image}`}
                            alt=""
                            className="alert-thumbnail"
                            onError={(e) => {
                              const target = e.currentTarget;
                              target.onerror = null;
                              target.style.display = 'none';
                              const placeholder = target.nextElementSibling as HTMLElement;
                              if (placeholder) placeholder.style.display = 'flex';
                            }}
                          />
                        ) : null}
                        <div
                          className="alert-thumbnail-placeholder"
                          style={{ display: alert.alert_image ? 'none' : 'flex' }}
                        >
                          {getAlertTypeIcon(alert.alert_type)}
                        </div>
                        <div className="alert-item-info">
                          <div className="alert-item-header">
                            <span className="alert-task-name">{task?.name || `任务 #${alert.task_id}`}</span>
                            {index === 0 && <span className="latest-badge">最新</span>}
                          </div>
                          <div className="alert-item-meta">
                            <span className="alert-time">
                              <RelativeTime time={alert.alert_time} />
                            </span>
                            <AlertTypeBadge type={alert.alert_type} showIcon={false} />
                          </div>
                          {alert.detection_count > 1 && (
                            <div className="alert-detection-count">
                              <AppstoreOutlined />
                              检测{alert.detection_count}次
                            </div>
                          )}
                          {alert.alert_message && (
                            <p className="alert-message">{alert.alert_message}</p>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
            </div>

            {/* 底部刷新指示 */}
            <div className="list-footer">
              <SyncOutlined spin />
              每 <span>5</span> 秒自动刷新
            </div>
          </div>
        </div>
      </div>

      {/* 粒子效果 */}
      <ParticlesEffect />
    </div>
  );
};

// 粒子效果组件
const ParticlesEffect: React.FC = () => {
  useEffect(() => {
    const container = document.querySelector('.particles-container');
    if (!container) return;

    const particleCount = 30;

    const createParticle = () => {
      const particle = document.createElement('div');
      particle.className = 'particle';

      const size = Math.random() * 4 + 2;
      const left = Math.random() * 100;
      const duration = Math.random() * 15 + 10;
      const delay = Math.random() * 10;

      particle.style.cssText = `
        width: ${size}px;
        height: ${size}px;
        left: ${left}%;
        animation-duration: ${duration}s;
        animation-delay: -${delay}s;
      `;

      container.appendChild(particle);

      setTimeout(() => {
        particle.remove();
        createParticle();
      }, (duration + delay) * 1000);
    };

    for (let i = 0; i < particleCount; i++) {
      createParticle();
    }

    return () => {
      if (container) container.innerHTML = '';
    };
  }, []);

  return null;
};

export default AlertWallPage;
