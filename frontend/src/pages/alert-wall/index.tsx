import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Image } from 'antd';
import { getAlerts, getTodayAlertsCount, getVideoSources, getWorkflows, getAlertTrend } from '@/services/api';
import { Alert, Task } from '../alerts/types';
import AlertTypeBadge from '../alerts/components/AlertTypeBadge';
import RelativeTime from '../alerts/components/RelativeTime';
import { buildAlertVideoUrls } from '@/utils/media';
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
  FireOutlined,
  BranchesOutlined,
  LineChartOutlined,
  PlayCircleOutlined,
  FileImageOutlined,
  TagOutlined,
  ApartmentOutlined,
  LeftOutlined,
  RightOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import AppButton from '@/components/common/AppButton';
import AppModal from '@/components/common/AppModal';
import './index.css';

/* ---------- 主题 ---------- */

const AW_THEMES = [
  { key: 'blue', name: '科技蓝', color: '#3b82f6' },
  { key: 'emerald', name: '翡翠绿', color: '#10b981' },
  { key: 'violet', name: '暗夜紫', color: '#8b5cf6' },
  { key: 'amber', name: '琥珀金', color: '#f59e0b' },
  { key: 'cyan', name: '深青', color: '#22d3ee' },
] as const;

type AwThemeKey = (typeof AW_THEMES)[number]['key'];

const AW_THEME_STORAGE_KEY = 'aw-theme';

const resolveInitialTheme = (): AwThemeKey => {
  const keys: string[] = AW_THEMES.map(t => t.key);
  const fromUrl = new URLSearchParams(window.location.search).get('theme');
  if (fromUrl && keys.includes(fromUrl)) return fromUrl as AwThemeKey;
  const stored = localStorage.getItem(AW_THEME_STORAGE_KEY);
  if (stored && keys.includes(stored)) return stored as AwThemeKey;
  return 'blue';
};

const ThemeSwitcher: React.FC<{ theme: AwThemeKey; onChange: (theme: AwThemeKey) => void }> = ({ theme, onChange }) => (
  <div className="aw-theme-switcher">
    {AW_THEMES.map(t => (
      <button
        key={t.key}
        type="button"
        className={`aw-theme-dot ${t.key === theme ? 'active' : ''}`}
        style={{ background: t.color }}
        title={t.name}
        aria-label={`切换到${t.name}主题`}
        onClick={() => onChange(t.key)}
      />
    ))}
  </div>
);

/* ---------- 数据解析 helpers ---------- */

interface DetectionImageItem {
  image_path: string;
  image_url?: string;
  detection_time?: string;
}

const safeParseJson = <T,>(value: unknown, fallback: T): T => {
  if (typeof value === 'string') {
    if (!value.trim()) return fallback;
    try {
      return JSON.parse(value) as T;
    } catch {
      return fallback;
    }
  }
  return (value as T) ?? fallback;
};

const parseDetectionImages = (raw: Alert['detection_images']): DetectionImageItem[] => {
  const parsed = safeParseJson<unknown>(raw, []);
  if (!Array.isArray(parsed)) return [];
  return parsed.flatMap((item: any) => {
    if (typeof item === 'string') return [{ image_path: item }];
    if (item?.image_path) {
      return [{
        image_path: item.image_path,
        image_url: item.image_url,
        detection_time: item.detection_time,
      }];
    }
    return [];
  });
};

/* ---------- 媒体项 ---------- */

type WallMediaItem =
  | { key: string; label: string; type: 'image'; src: string }
  | { key: string; label: string; type: 'video'; candidates: string[]; rawPath: string };

const buildMediaItems = (alert: Alert): WallMediaItem[] => {
  const items: WallMediaItem[] = [];
  if (alert.alert_image_url || alert.alert_image) {
    items.push({
      key: 'alert-image',
      label: '告警截图',
      type: 'image',
      src: alert.alert_image_url || `/api/image/frames/${alert.alert_image}`,
    });
  }
  if (alert.alert_video_url || alert.alert_video) {
    items.push({
      key: 'alert-video',
      label: '告警视频',
      type: 'video',
      candidates: alert.alert_video_url ? [alert.alert_video_url] : buildAlertVideoUrls(alert.alert_video),
      rawPath: alert.alert_video || '',
    });
  }
  if (alert.alert_image_ori_url || alert.alert_image_ori) {
    items.push({
      key: 'origin-image',
      label: '原始画面',
      type: 'image',
      src: alert.alert_image_ori_url || `/api/image/frames/${alert.alert_image_ori}`,
    });
  }
  return items;
};

/* ---------- 内嵌视频(多地址回退) ---------- */

const WallVideoPreview: React.FC<{ candidates: string[]; rawPath: string }> = ({ candidates, rawPath }) => {
  const [activeIndex, setActiveIndex] = useState(0);
  const [failed, setFailed] = useState(false);
  const candidateKey = candidates.join('|');

  useEffect(() => {
    setActiveIndex(0);
    setFailed(false);
  }, [rawPath, candidateKey]);

  const currentSrc = candidates[activeIndex] || '';

  if (!currentSrc || failed) {
    return (
      <div className="aw-video-fallback">
        <VideoCameraOutlined />
        <strong>{failed ? '视频无法播放' : '视频暂不可用'}</strong>
        <span>{failed ? '浏览器无法解码该编码,告警录像需要 H.264' : '未生成可播放的视频地址'}</span>
        {failed && (
          <div className="aw-video-fallback-actions">
            <button
              type="button"
              className="aw-nav-button"
              onClick={() => { setActiveIndex(0); setFailed(false); }}
            >
              <ReloadOutlined /> 重试
            </button>
            {candidates[0] && (
              <a className="aw-nav-button" href={candidates[0]} target="_blank" rel="noopener noreferrer">
                新窗口打开
              </a>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <video
      className="aw-video"
      controls
      preload="metadata"
      src={currentSrc}
      onError={() => {
        if (activeIndex < candidates.length - 1) {
          setActiveIndex(prev => prev + 1);
        } else {
          setFailed(true);
        }
      }}
    />
  );
};

/* ---------- 告警详情弹窗 ---------- */

interface AlertDetailModalProps {
  alert: Alert;
  task?: Task;
  visible: boolean;
  currentIndex: number;
  total: number;
  onNavigate: (direction: 'prev' | 'next') => void;
  onClose: () => void;
}

const AlertDetailModal: React.FC<AlertDetailModalProps> = ({
  alert,
  task,
  visible,
  currentIndex,
  total,
  onNavigate,
  onClose,
}) => {
  const [activeMediaKey, setActiveMediaKey] = useState('');

  // 切换告警时回到默认媒体
  useEffect(() => {
    setActiveMediaKey('');
  }, [alert.id]);

  if (!visible) return null;

  const detectionImages = parseDetectionImages(alert.detection_images);
  const windowStats = safeParseJson<Record<string, any> | null>(alert.window_stats, null);
  const mediaItems = buildMediaItems(alert);
  const activeMedia = mediaItems.find(m => m.key === activeMediaKey) || mediaItems[0];

  const computedRatio = typeof windowStats?.detection_ratio === 'number'
    ? windowStats.detection_ratio * 100
    : ((windowStats?.detection_count || 0) / Math.max(windowStats?.total_count || 1, 1)) * 100;
  const ratioPercent = Number.isFinite(computedRatio) ? Math.min(100, Math.max(0, computedRatio)) : 0;

  const modalTitle = (
    <div className="aw-modal-toolbar">
      <span className="aw-modal-title">告警详情</span>
      <div className="aw-modal-nav">
        <span className="aw-modal-position">{currentIndex + 1} / {total}</span>
        <button
          type="button"
          className="aw-nav-button"
          disabled={currentIndex <= 0}
          onClick={() => onNavigate('prev')}
        >
          <LeftOutlined /> 上一条
        </button>
        <button
          type="button"
          className="aw-nav-button"
          disabled={currentIndex >= total - 1}
          onClick={() => onNavigate('next')}
        >
          下一条 <RightOutlined />
        </button>
      </div>
    </div>
  );

  return (
    <AppModal
      open={visible}
      onCancel={onClose}
      footer={null}
      title={modalTitle}
      description={task?.name || `任务 #${alert.task_id}`}
      kind="detail"
      size="xl"
      className="alert-wall-detail-modal"
    >
        <div className="alert-wall-modal-content">
          {/* 基本信息 */}
          <div className="detail-section">
            <h3 className="section-title">
              <TagOutlined />
              基本信息
            </h3>
            <div className="info-grid">
              <div className="info-row">
                <span className="info-label">视频源</span>
                <span className="info-value">{task?.name || `任务 #${alert.task_id}`}</span>
              </div>
              {alert.workflow_name && (
                <div className="info-row">
                  <span className="info-label">算法编排</span>
                  <span className="info-value workflow-value">{alert.workflow_name}</span>
                </div>
              )}
              <div className="info-row">
                <span className="info-label">告警时间</span>
                <span className="info-value">
                  {new Date(alert.alert_time).toLocaleString('zh-CN', {
                    year: 'numeric',
                    month: '2-digit',
                    day: '2-digit',
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit',
                    hour12: false,
                  })}
                </span>
              </div>
              <div className="info-row">
                <span className="info-label">告警类型</span>
                <span className="info-value">
                  <AlertTypeBadge type={alert.alert_type} showIcon />
                </span>
              </div>
              <div className="info-row">
                <span className="info-label">检测帧数</span>
                <span className="info-value">{alert.detection_count} 帧</span>
              </div>
              {alert.alert_message && (
                <div className="info-row full-width">
                  <span className="info-label">告警消息</span>
                  <span className="info-value" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' as const }}>{alert.alert_message}</span>
                </div>
              )}
            </div>
          </div>

          {/* 现场画面:主舞台 + 缩略图栏 */}
          {mediaItems.length > 0 && activeMedia && (
            <div className="detail-section">
              <h3 className="section-title">
                <PlayCircleOutlined />
                现场画面
              </h3>
              <div className="aw-media-layout">
                <div className="aw-media-stage">
                  <span className="aw-media-stage-label">{activeMedia.label}</span>
                  {activeMedia.type === 'image' ? (
                    <Image
                      src={activeMedia.src}
                      alt={activeMedia.label}
                      preview={{ title: activeMedia.label }}
                    />
                  ) : (
                    <WallVideoPreview candidates={activeMedia.candidates} rawPath={activeMedia.rawPath} />
                  )}
                </div>
                {mediaItems.length > 1 && (
                  <div className="aw-media-rail">
                    {mediaItems.map(item => (
                      <button
                        key={item.key}
                        type="button"
                        className={`aw-media-thumb ${item.key === activeMedia.key ? 'active' : ''}`}
                        onClick={() => setActiveMediaKey(item.key)}
                        title={item.label}
                      >
                        {item.type === 'image' ? (
                          <img src={item.src} alt={item.label} loading="lazy" />
                        ) : (
                          <span className="aw-media-thumb-video">
                            <PlayCircleOutlined />
                          </span>
                        )}
                        <span className="aw-media-thumb-label">{item.label}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* 检测序列 */}
          {detectionImages.length > 0 && (
            <div className="detail-section">
              <h3 className="section-title">
                <FileImageOutlined />
                检测序列 ({detectionImages.length})
              </h3>
              <div className="detection-images-grid">
                {detectionImages.map((img, idx) => (
                  <div key={`${img.image_path}-${idx}`} className="detection-image-item">
                    <Image
                      src={img.image_url || `/api/image/frames/${img.image_path}`}
                      alt={`检测图片 ${idx + 1}`}
                      className="detection-image"
                      preview={{ title: `第 ${idx + 1} 次检测` }}
                    />
                    {img.detection_time && (
                      <div className="detection-image-time">
                        #{idx + 1} {new Date(img.detection_time).toLocaleTimeString('zh-CN', { hour12: false })}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 窗口统计 */}
          {windowStats && (
            <div className="detail-section">
              <h3 className="section-title">
                <LineChartOutlined />
                窗口统计
              </h3>
              <div className="window-stats">
                <div className="stat-box">
                  <div className="stat-box-label">检测帧数</div>
                  <div className="stat-box-value">{windowStats.detection_count || 0}</div>
                </div>
                <div className="stat-box">
                  <div className="stat-box-label">总帧数</div>
                  <div className="stat-box-value">{windowStats.total_count || 0}</div>
                </div>
                <div className="stat-box">
                  <div className="stat-box-label">检测比例</div>
                  <div className="stat-box-value">{ratioPercent.toFixed(1)}%</div>
                </div>
                <div className="stat-box">
                  <div className="stat-box-label">最大连续</div>
                  <div className="stat-box-value">{windowStats.max_consecutive || 0}</div>
                </div>
              </div>
            </div>
          )}
        </div>
    </AppModal>
  );
};

/* ---------- 独立时钟(避免整页每秒重渲染) ---------- */

const WallClock: React.FC = () => {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="time-display">
      <div className="time-value digital-font">
        {now.toLocaleTimeString('zh-CN', { hour12: false })}
      </div>
      <div className="date-value">
        {now.toLocaleDateString('zh-CN', {
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
          weekday: 'short',
        })}
      </div>
    </div>
  );
};

/* ---------- 大屏页面 ---------- */

const AlertWallPage: React.FC = () => {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [mainAlert, setMainAlert] = useState<Alert | null>(null);
  const [todayCount, setTodayCount] = useState(0);
  const [selectedAlert, setSelectedAlert] = useState<Alert | null>(null);
  const [showDetailModal, setShowDetailModal] = useState(false);
  const [totalCount, setTotalCount] = useState(0);
  const [videoSourceCount, setVideoSourceCount] = useState(0);
  const [activeWorkflowCount, setActiveWorkflowCount] = useState(0);
  const [alertTrend, setAlertTrend] = useState<Array<{ date: string; count: number }>>([]);
  const [isNewAlert, setIsNewAlert] = useState(false);
  const [isManualSelect, setIsManualSelect] = useState(false);
  const [imageError, setImageError] = useState(false);
  const [theme, setTheme] = useState<AwThemeKey>(resolveInitialTheme);
  const mainDisplayRef = useRef<HTMLDivElement>(null);
  const logoRef = useRef<HTMLDivElement>(null);
  const lastKnownLatestIdRef = useRef<string | undefined>(undefined);
  const mainAlertRef = useRef<Alert | null>(null);
  const alertIdsRef = useRef('');

  // 主题挂在 <html> 上:portal 到 body 的详情弹窗也能继承 :root 上的主题变量
  useEffect(() => {
    document.documentElement.dataset.awTheme = theme;
    localStorage.setItem(AW_THEME_STORAGE_KEY, theme);
    return () => {
      delete document.documentElement.dataset.awTheme;
    };
  }, [theme]);

  useEffect(() => {
    mainAlertRef.current = mainAlert;
  }, [mainAlert]);

  // 快循环:告警列表 + 今日数量
  const loadFast = useCallback(async () => {
    try {
      const [alertsResponse, todayCountResponse] = await Promise.all([
        getAlerts({ page: 1, per_page: 50 }),
        getTodayAlertsCount(),
      ]);

      const newAlerts: Alert[] = alertsResponse.data || [];

      // id 序列未变化时跳过 setState,避免每 5 秒重渲染整个列表
      const idsSignature = newAlerts.map(a => a.id).join(',');
      if (idsSignature !== alertIdsRef.current) {
        alertIdsRef.current = idsSignature;
        setAlerts(newAlerts);
        setTotalCount(alertsResponse.pagination?.total || 0);
      }

      const newTodayCount = todayCountResponse.count || 0;
      setTodayCount(prev => (prev === newTodayCount ? prev : newTodayCount));

      // 检查是否有真正的新告警(而不是用户手动切换历史)
      const currentLatestId = newAlerts.length > 0 ? newAlerts[0].id : undefined;
      const hasNewAlert = Boolean(
        currentLatestId && lastKnownLatestIdRef.current && currentLatestId !== lastKnownLatestIdRef.current
      );

      // 更新已知的最新告警ID
      if (currentLatestId && currentLatestId !== lastKnownLatestIdRef.current) {
        lastKnownLatestIdRef.current = currentLatestId;
      }

      // 只有在真正有新告警时才自动切换并播放动画
      if (hasNewAlert) {
        setIsNewAlert(true);
        setIsManualSelect(false); // 重置手动选择标志
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
      } else if (!mainAlertRef.current && newAlerts.length > 0) {
        // 首次加载,设置最新告警
        setMainAlert(newAlerts[0]);
        lastKnownLatestIdRef.current = currentLatestId;
      }

      // 重置图片错误状态
      setImageError(false);
    } catch (error) {
      console.error('加载告警数据失败:', error);
    }
  }, []);

  // 慢循环:任务/编排/趋势等低频数据
  const loadSlow = useCallback(async () => {
    try {
      const [tasksResponse, workflowsResponse, trendResponse] = await Promise.all([
        getVideoSources(),
        getWorkflows(),
        getAlertTrend(7),
      ]);

      setTasks(tasksResponse || []);
      setVideoSourceCount(tasksResponse?.length || 0);
      setActiveWorkflowCount(workflowsResponse?.filter((w: any) => w.is_active).length || 0);
      setAlertTrend(trendResponse?.trend || []);
    } catch (error) {
      console.error('加载基础数据失败:', error);
    }
  }, []);

  // 初始化
  useEffect(() => {
    loadFast();
    loadSlow();

    const fastInterval = setInterval(loadFast, 5000);
    const slowInterval = setInterval(loadSlow, 60000);

    return () => {
      clearInterval(fastInterval);
      clearInterval(slowInterval);
    };
  }, [loadFast, loadSlow]);

  // 选择告警
  const selectAlert = (index: number) => {
    if (index >= 0 && index < alerts.length) {
      setIsManualSelect(true); // 标记为手动选择
      setIsNewAlert(false); // 确保不触发新告警动画

      // 移除可能存在的 flash-border 类
      if (mainDisplayRef.current) {
        mainDisplayRef.current.classList.remove('flash-border');
      }

      setMainAlert(alerts[index]);
      setImageError(false);
    }
  };

  // 查看告警详情
  const viewAlertDetail = (alert: Alert, e: React.MouseEvent) => {
    e.stopPropagation(); // 防止触发选择事件
    setSelectedAlert(alert);
    setShowDetailModal(true);
  };

  // 关闭详情弹窗
  const closeDetailModal = () => {
    setShowDetailModal(false);
    setTimeout(() => setSelectedAlert(null), 300); // 等待动画完成
  };

  // 详情弹窗内上一条/下一条
  const navigateDetail = useCallback((direction: 'prev' | 'next') => {
    setSelectedAlert(prev => {
      if (!prev) return prev;
      const idx = alerts.findIndex(a => a.id === prev.id);
      if (idx === -1) return prev;
      const nextIdx = direction === 'prev' ? idx - 1 : idx + 1;
      return nextIdx >= 0 && nextIdx < alerts.length ? alerts[nextIdx] : prev;
    });
  }, [alerts]);

  // 处理图片加载错误
  const handleImageError = () => {
    setImageError(true);
  };

  const mainTask = mainAlert ? tasks.find(t => t.id === mainAlert.task_id) : null;
  const selectedIndex = selectedAlert ? alerts.findIndex(a => a.id === selectedAlert.id) : -1;

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
              <div className="stat-icon-wrapper video-icon">
                <VideoCameraOutlined />
              </div>
              <div className="stat-content">
                <div className="stat-value digital-font" id="videoSourceCount">{videoSourceCount}</div>
                <div className="stat-label">视频源</div>
              </div>
            </div>
            <div className="stat-item">
              <div className="stat-icon-wrapper workflow-icon">
                <BranchesOutlined />
              </div>
              <div className="stat-content">
                <div className="stat-value digital-font" id="activeWorkflowCount">{activeWorkflowCount}</div>
                <div className="stat-label">算法编排</div>
              </div>
            </div>
            <div className="stat-item trend-item">
              <div className="trend-content">
                <div className="trend-label">近7日趋势</div>
                <TrendChart data={alertTrend} />
              </div>
            </div>
          </div>

          <div className="header-right">
            <div className="live-badge">
              <div className="status-dot" />
              <span>LIVE</span>
            </div>
            <WallClock />
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
                {mainAlert?.workflow_name && (
                  <div className="info-item workflow-item">
                    <ApartmentOutlined />
                    <span>{mainAlert.workflow_name}</span>
                  </div>
                )}
                <div className="info-item">
                  <ClockCircleOutlined />
                  <span>
                    {mainAlert ? new Date(mainAlert.alert_time).toLocaleString('zh-CN', {
                      month: '2-digit',
                      day: '2-digit',
                      hour: '2-digit',
                      minute: '2-digit',
                      second: '2-digit',
                      hour12: false,
                    }) : '--'}
                  </span>
                </div>
              </div>
            </div>

            {/* 图片展示区 */}
            <div className="main-display-image">
              {mainAlert ? (
                (mainAlert.alert_image_url || mainAlert.alert_image) && !imageError ? (
                  <img
                    key={mainAlert.id}
                    src={mainAlert.alert_image_url || `/api/image/frames/${mainAlert.alert_image}`}
                    alt="Alert"
                    className={`main-image ${isNewAlert && !isManualSelect ? 'alert-switch-animation with-animation' : ''}`}
                    onError={handleImageError}
                  />
                ) : (
                  <div className={`no-alert-placeholder ${isNewAlert && !isManualSelect ? 'alert-switch-animation' : ''}`}>
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

            {/* 底部信息栏 */}
            {mainAlert && (
              <div className="main-display-footer">
                <div className="footer-left">
                  <AlertTypeBadge type={mainAlert.alert_type} showIcon />
                  <span className="detection-count">检测 {mainAlert.detection_count} 帧</span>
                </div>
                {mainAlert.alert_message && (
                  <div className="footer-message" title={mainAlert.alert_message}>
                    {mainAlert.alert_message}
                  </div>
                )}
              </div>
            )}
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
                      className={`alert-list-item ${index === 0 ? 'latest' : ''} ${alert.id === mainAlert?.id ? 'active' : ''}`}
                      onClick={() => selectAlert(index)}
                    >
                      <div className="alert-item-content">
                        {alert.alert_image_url || alert.alert_image ? (
                          <img
                            src={alert.alert_image_url || `/api/image/frames/${alert.alert_image}`}
                            alt=""
                            className="alert-thumbnail"
                            loading="lazy"
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
                            <AppButton
                              size="small"
                              tone="info"
                              variant="text"
                              className="detail-button"
                              aria-label={`查看 ${task?.name || `任务 #${alert.task_id}`} 的告警详情`}
                              onClick={(e) => viewAlertDetail(alert, e)}
                              title="查看详情"
                            >
                              <TagOutlined />
                              <span>详情</span>
                            </AppButton>
                          </div>
                          {alert.workflow_name && (
                            <div className="alert-workflow">
                              <ApartmentOutlined />
                              <span>{alert.workflow_name}</span>
                            </div>
                          )}
                          <div className="alert-item-meta">
                            <span className="alert-time">
                              <RelativeTime time={alert.alert_time} />
                            </span>
                            <AlertTypeBadge type={alert.alert_type} showIcon={false} />
                          </div>
                          {alert.detection_count > 1 && (
                            <div className="alert-detection-count">
                              <AppstoreOutlined />
                              检测{alert.detection_count}帧
                            </div>
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

      {/* 主题切换器 */}
      <ThemeSwitcher theme={theme} onChange={setTheme} />

      {/* 告警详情弹窗 */}
      {selectedAlert && (
        <AlertDetailModal
          alert={selectedAlert}
          task={tasks.find(t => t.id === selectedAlert.task_id)}
          visible={showDetailModal}
          currentIndex={selectedIndex === -1 ? 0 : selectedIndex}
          total={alerts.length}
          onNavigate={navigateDetail}
          onClose={closeDetailModal}
        />
      )}
    </div>
  );
};

// 趋势图组件
const TrendChart: React.FC<{ data: Array<{ date: string; count: number }> }> = ({ data }) => {
  if (!data || data.length === 0) {
    return (
      <div className="trend-chart-placeholder">
        <LineChartOutlined />
        <span>暂无数据</span>
      </div>
    );
  }

  const maxValue = Math.max(...data.map(d => d.count), 1);
  const width = 280;
  const height = 60;
  const padding = 5;

  // 生成SVG路径
  const points = data.map((d, i) => {
    const x = (i / (data.length - 1)) * (width - 2 * padding) + padding;
    const y = height - padding - (d.count / maxValue) * (height - 2 * padding);
    return `${x},${y}`;
  }).join(' ');

  // 生成填充区域
  const areaPoints = `
    ${padding},${height - padding}
    ${points}
    ${width - padding},${height - padding}
  `;

  return (
    <div className="trend-chart">
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
        <defs>
          <linearGradient id="trendGradient" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" className="trend-stop-start" />
            <stop offset="100%" className="trend-stop-end" />
          </linearGradient>
        </defs>
        {/* 填充区域 */}
        <polygon
          points={areaPoints}
          fill="url(#trendGradient)"
        />
        {/* 折线 */}
        <polyline
          className="trend-line"
          points={points}
        />
        {/* 数据点 */}
        {data.map((d, i) => {
          const x = (i / (data.length - 1)) * (width - 2 * padding) + padding;
          const y = height - padding - (d.count / maxValue) * (height - 2 * padding);
          return (
            <circle
              key={i}
              className="trend-dot"
              cx={x}
              cy={y}
              r="3"
            />
          );
        })}
      </svg>
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
