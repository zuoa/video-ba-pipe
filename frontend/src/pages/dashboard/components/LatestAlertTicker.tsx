import React, { useEffect, useMemo, useState } from 'react';
import { RightOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import './LatestAlertTicker.css';

export interface TickerAlert {
  id: number;
  task_id: number;
  alert_type: string;
  alert_message: string;
  alert_time: string;
}

export interface TickerTask {
  id: number;
  name: string;
  source_code: string;
}

interface LatestAlertTickerProps {
  alerts: TickerAlert[];
  tasks: TickerTask[];
  loading?: boolean;
  maxItems?: number;
  viewAllPath?: string;
}

const TICK_INTERVAL_MS = 3600;

const getSeverityClass = (type: string) => {
  const normalizedType = type.toLowerCase();
  if (normalizedType === 'critical' || normalizedType === 'error') return 'is-critical';
  if (normalizedType === 'warning') return 'is-warning';
  return 'is-info';
};

const LatestAlertTicker: React.FC<LatestAlertTickerProps> = ({
  alerts,
  tasks,
  loading = false,
  maxItems = 3,
  viewAllPath = '/alert-wall',
}) => {
  const [activeIndex, setActiveIndex] = useState(0);
  const visibleAlerts = alerts.slice(0, maxItems);
  const taskById = useMemo(
    () => new Map(tasks.map((task) => [task.id, task])),
    [tasks],
  );

  useEffect(() => {
    const prefersReducedMotion = window.matchMedia(
      '(prefers-reduced-motion: reduce)',
    ).matches;
    if (visibleAlerts.length <= 1 || prefersReducedMotion) return undefined;

    const interval = window.setInterval(() => {
      setActiveIndex((currentIndex) => (currentIndex + 1) % visibleAlerts.length);
    }, TICK_INTERVAL_MS);

    return () => window.clearInterval(interval);
  }, [visibleAlerts.length]);

  const activeAlert = visibleAlerts[activeIndex % Math.max(visibleAlerts.length, 1)];

  if (!activeAlert) {
    return (
      <div className="latest-alert-ticker latest-alert-ticker--empty">
        <span className="latest-alert-ticker__label">
          <i aria-hidden="true" />
          最新告警
        </span>
        <span>{loading ? '读取中…' : '暂无最新告警'}</span>
      </div>
    );
  }

  const task = taskById.get(activeAlert.task_id);
  const taskName = task
    ? `${task.name} #${task.source_code}`
    : `视频源 #${activeAlert.task_id}`;
  const accessibleLabel = [
    taskName,
    activeAlert.alert_type,
    dayjs(activeAlert.alert_time).format('MM月DD日 HH:mm'),
  ].join('，');

  return (
    <a
      className="latest-alert-ticker"
      href={viewAllPath}
      aria-label={`查看最新告警：${accessibleLabel}`}
      title={activeAlert.alert_message || accessibleLabel}
    >
      <span className="latest-alert-ticker__label">
        <i className={getSeverityClass(activeAlert.alert_type)} aria-hidden="true" />
        最新告警
      </span>
      <span
        key={`${activeAlert.id}-${activeIndex}`}
        className="latest-alert-ticker__viewport"
      >
        <span className="latest-alert-ticker__message">
          <strong>{taskName}</strong>
          <small>{activeAlert.alert_type}</small>
        </span>
      </span>
      <time dateTime={activeAlert.alert_time}>
        {dayjs(activeAlert.alert_time).format('HH:mm')}
      </time>
      <RightOutlined className="latest-alert-ticker__arrow" aria-hidden="true" />
    </a>
  );
};

export default LatestAlertTicker;
