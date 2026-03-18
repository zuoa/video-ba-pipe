import React, { useEffect, useState } from 'react';
import { Modal, Image, Space, Button, Typography } from 'antd';
import {
  LeftOutlined,
  RightOutlined,
  InfoCircleOutlined,
  VideoCameraOutlined,
  PlayCircleOutlined,
  FileImageOutlined,
  ApartmentOutlined,
  ClockCircleOutlined,
  AlertOutlined,
  DashboardOutlined,
  FileTextOutlined,
  NumberOutlined,
  LinkOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { Alert, Task, DetectionImage, WindowStats, getAlertTypeConfig } from '../types';
import { buildAlertVideoUrls } from '@/utils/media';
import './AlertDetailModal.css';

const { Title, Text } = Typography;

interface AlertDetailModalProps {
  visible: boolean;
  alert: Alert | null;
  tasks: Task[];
  currentIndex: number;
  total: number;
  onClose: () => void;
  onNavigate: (direction: 'prev' | 'next') => void;
}

type MediaItem =
  | {
      key: string;
      label: string;
      type: 'image';
      src: string;
      previewTitle: string;
    }
  | {
      key: string;
      label: string;
      type: 'video';
      srcCandidates: string[];
      rawPath: string;
    };

const safeParseJson = <T,>(value: string | T | undefined, fallback: T): T => {
  if (typeof value === 'string') {
    if (!value.trim()) return fallback;
    try {
      return JSON.parse(value) as T;
    } catch {
      return fallback;
    }
  }

  return value ?? fallback;
};

const formatDateTime = (value?: string) => {
  if (!value) return '-';

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;

  return date.toLocaleString('zh-CN', { hour12: false });
};

const formatClockTime = (value?: string) => {
  if (!value) return '-';

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;

  return date.toLocaleTimeString('zh-CN', { hour12: false });
};

const VideoPreview: React.FC<{
  title: string;
  candidates: string[];
  rawPath: string;
  compact?: boolean;
}> = ({ title, candidates, rawPath, compact = false }) => {
  const [activeIndex, setActiveIndex] = useState(0);
  const [failed, setFailed] = useState(false);
  const candidateKey = candidates.join('|');

  useEffect(() => {
    setActiveIndex(0);
    setFailed(false);
  }, [rawPath, candidateKey]);

  const currentSrc = candidates[activeIndex] || '';
  const hasAlternative = activeIndex < candidates.length - 1;

  const handleError = () => {
    if (hasAlternative) {
      setActiveIndex(prev => prev + 1);
      return;
    }

    setFailed(true);
  };

  const handleRetry = () => {
    setActiveIndex(0);
    setFailed(false);
  };

  if (!currentSrc) {
    return (
      <div className={`alertDetail__videoFallback ${compact ? 'is-compact' : ''}`}>
        <VideoCameraOutlined />
        <strong>{title}暂不可用</strong>
        <span>未生成可播放的视频地址。</span>
      </div>
    );
  }

  if (failed) {
    return (
      <div className={`alertDetail__videoFallback ${compact ? 'is-compact' : ''}`}>
        <VideoCameraOutlined />
        <strong>{title}加载失败</strong>
        <span className="alertDetail__videoFallbackPath">{rawPath}</span>
        <div className="alertDetail__videoFallbackActions">
          <Button size="small" icon={<ReloadOutlined />} onClick={handleRetry}>
            重试
          </Button>
          <Button size="small" type="link" icon={<LinkOutlined />} href={currentSrc} target="_blank">
            新窗口打开
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="alertDetail__videoShell">
      <video controls preload="metadata" src={currentSrc} onError={handleError} />
      <div className="alertDetail__videoMeta">
        <span>{hasAlternative ? `正在尝试地址 ${activeIndex + 1}/${candidates.length}` : '视频地址已就绪'}</span>
        <a href={currentSrc} target="_blank" rel="noreferrer">
          打开原视频
        </a>
      </div>
    </div>
  );
};

const AlertDetailModal: React.FC<AlertDetailModalProps> = ({
  visible,
  alert,
  tasks,
  currentIndex,
  total,
  onClose,
  onNavigate,
}) => {
  if (!alert) return null;

  const task = tasks.find(t => t.id === alert.task_id);
  const taskName = task?.name || `任务 #${alert.task_id}`;
  const alertTypeConfig = getAlertTypeConfig(alert.alert_type);
  const windowStats = safeParseJson<Partial<WindowStats>>(alert.window_stats, {});
  const detectionImages = safeParseJson<DetectionImage[]>(alert.detection_images, []);
  const hasWindowStats = Object.keys(windowStats).length > 0;

  const computedRatio = typeof windowStats.detection_ratio === 'number'
    ? windowStats.detection_ratio * 100
    : ((windowStats.detection_count || 0) / Math.max(windowStats.total_count || 1, 1)) * 100;
  const ratioPercent = Number.isFinite(computedRatio) ? Math.min(100, Math.max(0, computedRatio)) : 0;

  const mediaItems: MediaItem[] = [];

  if (alert.alert_image) {
    mediaItems.push({
      key: 'alert-image',
      label: '告警截图',
      type: 'image',
      src: `/api/image/frames/${alert.alert_image}`,
      previewTitle: '告警截图',
    });
  }

  if (alert.alert_video) {
    mediaItems.push({
      key: 'alert-video',
      label: '告警视频',
      type: 'video',
      srcCandidates: buildAlertVideoUrls(alert.alert_video),
      rawPath: alert.alert_video,
    });
  }

  if (alert.alert_image_ori) {
    mediaItems.push({
      key: 'origin-image',
      label: '原始画面',
      type: 'image',
      src: `/api/image/frames/${alert.alert_image_ori}`,
      previewTitle: '原始画面',
    });
  }

  const [primaryMedia, ...secondaryMedia] = mediaItems;

  const metaItems = [
    {
      label: '告警时间',
      value: formatDateTime(alert.alert_time),
      icon: <ClockCircleOutlined />,
    },
    {
      label: '检测帧数',
      value: `${alert.detection_count} 帧`,
      icon: <DashboardOutlined />,
    },
    {
      label: '工作流',
      value: alert.workflow_id ? (alert.workflow_name || `流程编排 #${alert.workflow_id}`) : '未关联',
      icon: <ApartmentOutlined />,
    },
  ];

  const detailItems = [
    { label: '记录编号', value: `#${alert.id}` },
    { label: '任务名称', value: taskName },
    { label: '任务 ID', value: `#${alert.task_id}` },
    { label: '告警类型', value: alertTypeConfig.label },
    { label: '原始类型', value: alert.alert_type },
    { label: '工作流 ID', value: alert.workflow_id ? `#${alert.workflow_id}` : '未关联' },
  ];

  return (
    <Modal
      open={visible}
      onCancel={onClose}
      footer={null}
      width={1120}
      className="alertDetailModal"
      title={
        <div className="alertDetailModal__toolbar">
          <div className="alertDetailModal__toolbarTitle">
            <span className="alertDetailModal__titleIcon">
              <InfoCircleOutlined />
            </span>
            <div className="alertDetailModal__titleGroup">
              <span className="alertDetailModal__titleEyebrow">告警详情</span>
              <span className="alertDetailModal__titleMain">{taskName}</span>
            </div>
            <span className="alertDetailModal__position">
              {currentIndex + 1} / {total}
            </span>
          </div>
          <Space className="alertDetailModal__toolbarActions" size="small">
            <Button
              icon={<LeftOutlined />}
              onClick={() => onNavigate('prev')}
              disabled={currentIndex === 0}
            >
              上一条
            </Button>
            <Button
              icon={<RightOutlined />}
              onClick={() => onNavigate('next')}
              disabled={currentIndex === total - 1}
            >
              下一条
            </Button>
          </Space>
        </div>
      }
    >
      <div
        className="alertDetail"
        style={{
          ['--alert-accent' as string]: alertTypeConfig.color,
          ['--alert-accent-soft' as string]: alertTypeConfig.bgColor,
          ['--alert-accent-border' as string]: alertTypeConfig.borderColor,
        }}
      >
        <section className="alertDetail__summaryBand">
          <div className="alertDetail__summaryItem">
            <span className="alertDetail__summaryLabel">告警类型</span>
            <strong>{alertTypeConfig.label}</strong>
          </div>
          <div className="alertDetail__summaryItem">
            <span className="alertDetail__summaryLabel">发生时间</span>
            <strong>{formatDateTime(alert.alert_time)}</strong>
          </div>
          <div className="alertDetail__summaryItem">
            <span className="alertDetail__summaryLabel">检测帧数</span>
            <strong>{alert.detection_count} 帧</strong>
          </div>
          <div className="alertDetail__summaryItem">
            <span className="alertDetail__summaryLabel">记录编号</span>
            <strong>#{alert.id}</strong>
          </div>
        </section>

        <section className="alertDetail__hero">
          <div className="alertDetail__heroMain">
            <div className="alertDetail__eyebrow">
              <AlertOutlined />
              <span>事件摘要</span>
            </div>

            <div className="alertDetail__heroTags">
              <span className="alertDetail__typePill">{alertTypeConfig.label}</span>
              {alert.workflow_id && (
                <span className="alertDetail__workflowPill">
                  <ApartmentOutlined />
                  <span>{alert.workflow_name || `流程编排 #${alert.workflow_id}`}</span>
                </span>
              )}
            </div>

            <Title level={4} className="alertDetail__heroTitle">
              {taskName}
            </Title>

            <div className="alertDetail__messageCard">
              <div className="alertDetail__messageLabel">
                <FileTextOutlined />
                <span>告警描述</span>
              </div>
              <div className="alertDetail__messageText">
                {alert.alert_message || '暂无告警说明'}
              </div>
            </div>
          </div>

          <div className="alertDetail__heroAside">
            {metaItems.map(item => (
              <div key={item.label} className="alertDetail__metaCard">
                <div className="alertDetail__metaIcon">{item.icon}</div>
                <div className="alertDetail__metaContent">
                  <span className="alertDetail__metaLabel">{item.label}</span>
                  <span className="alertDetail__metaValue">{item.value}</span>
                </div>
              </div>
            ))}
          </div>
        </section>

        {primaryMedia && (
          <section className="alertDetail__panel">
            <div className="alertDetail__panelHeader">
              <div className="alertDetail__panelTitle">
                <PlayCircleOutlined />
                <span>现场画面</span>
              </div>
              <Text type="secondary">优先展示最能说明问题的告警素材</Text>
            </div>

            <div className="alertDetail__mediaLayout">
              <div className="alertDetail__mediaStage">
                <div className="alertDetail__mediaStageLabel">{primaryMedia.label}</div>
                <div className="alertDetail__mediaStageBody">
                  {primaryMedia.type === 'image' ? (
                    <Image
                      src={primaryMedia.src}
                      alt={primaryMedia.label}
                      preview={{ title: primaryMedia.previewTitle }}
                    />
                  ) : (
                    <VideoPreview
                      title={primaryMedia.label}
                      candidates={primaryMedia.srcCandidates}
                      rawPath={primaryMedia.rawPath}
                    />
                  )}
                </div>
              </div>

              {secondaryMedia.length > 0 && (
                <div className="alertDetail__mediaRail">
                  {secondaryMedia.map(item => (
                    <div key={item.key} className="alertDetail__mediaCard">
                      <div className="alertDetail__mediaCardLabel">{item.label}</div>
                      <div className="alertDetail__mediaCardBody">
                        {item.type === 'image' ? (
                          <Image
                            src={item.src}
                            alt={item.label}
                            preview={{ title: item.previewTitle }}
                          />
                        ) : (
                          <VideoPreview
                            title={item.label}
                            candidates={item.srcCandidates}
                            rawPath={item.rawPath}
                            compact
                          />
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>
        )}

        <div className="alertDetail__panelGrid">
          <section className="alertDetail__panel">
            <div className="alertDetail__panelHeader">
              <div className="alertDetail__panelTitle">
                <NumberOutlined />
                <span>事件信息</span>
              </div>
            </div>

            <div className="alertDetail__infoGrid">
              {detailItems.map(item => (
                <div key={item.label} className="alertDetail__infoItem">
                  <span className="alertDetail__infoLabel">{item.label}</span>
                  <span className="alertDetail__infoValue">{item.value}</span>
                </div>
              ))}
            </div>
          </section>

          {hasWindowStats && (
            <section className="alertDetail__panel">
              <div className="alertDetail__panelHeader">
                <div className="alertDetail__panelTitle">
                  <VideoCameraOutlined />
                  <span>窗口统计</span>
                </div>
              </div>

              <div className="alertDetail__statsGrid">
                <div className="alertDetail__statCard">
                  <span className="alertDetail__statLabel">检测帧数</span>
                  <strong className="alertDetail__statValue">
                    {windowStats.detection_count || 0}
                    <small> / {windowStats.total_count || 0}</small>
                  </strong>
                </div>
                <div className="alertDetail__statCard">
                  <span className="alertDetail__statLabel">检测比例</span>
                  <strong className="alertDetail__statValue">{ratioPercent.toFixed(1)}%</strong>
                </div>
                <div className="alertDetail__statCard">
                  <span className="alertDetail__statLabel">最大连续命中</span>
                  <strong className="alertDetail__statValue">{windowStats.max_consecutive || 0}</strong>
                </div>
              </div>

              <div className="alertDetail__ratioBlock">
                <div className="alertDetail__ratioHeader">
                  <span>窗口内命中密度</span>
                  <strong>{ratioPercent.toFixed(1)}%</strong>
                </div>
                <div className="alertDetail__ratioTrack">
                  <div
                    className="alertDetail__ratioBar"
                    style={{ width: `${ratioPercent}%` }}
                  />
                </div>
                <Text type="secondary">
                  以时间窗口内检测命中率与连续命中次数辅助判断告警稳定性。
                </Text>
              </div>
            </section>
          )}
        </div>

        {detectionImages.length > 0 && (
          <section className="alertDetail__panel">
            <div className="alertDetail__panelHeader">
              <div className="alertDetail__panelTitle">
                <FileImageOutlined />
                <span>检测序列</span>
              </div>
              <Text type="secondary">按触发顺序查看时间窗口中的关键帧</Text>
            </div>

            <div className="alertDetail__sequenceGrid">
              {detectionImages.map((img, index) => (
                <div key={`${img.image_path}-${index}`} className="alertDetail__sequenceCard">
                  <div className="alertDetail__sequenceThumb">
                    <Image
                      src={`/api/image/frames/${img.image_path}`}
                      alt={`检测 ${index + 1}`}
                      preview={{
                        title: `第 ${index + 1} 次检测`,
                      }}
                    />
                  </div>
                  <div className="alertDetail__sequenceMeta">
                    <span className="alertDetail__sequenceIndex">第 {index + 1} 次</span>
                    <span className="alertDetail__sequenceTime">
                      {formatClockTime(img.detection_time)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}
      </div>
    </Modal>
  );
};

export default AlertDetailModal;
