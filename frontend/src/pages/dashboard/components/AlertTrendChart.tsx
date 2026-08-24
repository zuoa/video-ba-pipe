import React, { useEffect, useMemo, useState } from 'react';
import { Segmented } from 'antd';
import { LineChartOutlined } from '@ant-design/icons';
import { getAlertTrend } from '@/services/api';
import type { AlertStatsPeriod } from '@/services/api';
import './AlertTrendChart.css';

interface TrendBucket {
  start: string;
  label: string;
  count: number;
  is_future?: boolean;
}

interface TrendResponse {
  period: AlertStatsPeriod;
  start: string;
  end: string;
  buckets: TrendBucket[];
}

interface ChartPoint extends TrendBucket {
  x: number;
  y: number;
}

const PERIOD_OPTIONS: Array<{ label: string; value: AlertStatsPeriod }> = [
  { label: '时', value: 'hour' },
  { label: '日', value: 'day' },
  { label: '周', value: 'week' },
  { label: '月', value: 'month' },
  { label: '年', value: 'year' },
];

const PERIOD_HINTS: Record<AlertStatsPeriod, string> = {
  hour: '本小时 · 每 5 分钟',
  day: '今日 · 每小时',
  week: '本周 · 每天',
  month: '本月 · 每天',
  year: '今年 · 每月',
};

const CHART_WIDTH = 960;
const CHART_HEIGHT = 230;
const PLOT = { left: 46, right: 18, top: 16, bottom: 34 };
const EMPTY_BUCKETS: TrendBucket[] = [];

const AlertTrendChart: React.FC = () => {
  const [period, setPeriod] = useState<AlertStatsPeriod>('day');
  const [trend, setTrend] = useState<TrendResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;

    const loadTrend = async () => {
      setLoading(true);
      try {
        const response = await getAlertTrend(period);
        if (active) {
          setTrend(response);
          setError('');
        }
      } catch (requestError) {
        console.error('加载告警趋势失败:', requestError);
        if (active) setError('趋势数据暂不可用');
      } finally {
        if (active) setLoading(false);
      }
    };

    loadTrend();
    const refreshTimer = window.setInterval(loadTrend, 30_000);
    return () => {
      active = false;
      window.clearInterval(refreshTimer);
    };
  }, [period]);

  const buckets = trend?.buckets ?? EMPTY_BUCKETS;
  const totalCount = buckets.reduce((sum, bucket) => sum + bucket.count, 0);
  const peakCount = Math.max(0, ...buckets.map((bucket) => bucket.count));
  const chartMax = Math.max(1, peakCount);

  const { points, visiblePoints, linePath, areaPath, tickIndexes, yTicks } = useMemo(() => {
    const plotWidth = CHART_WIDTH - PLOT.left - PLOT.right;
    const plotHeight = CHART_HEIGHT - PLOT.top - PLOT.bottom;
    const denominator = Math.max(buckets.length - 1, 1);
    const nextPoints: ChartPoint[] = buckets.map((bucket, index) => ({
      ...bucket,
      x: PLOT.left + (index / denominator) * plotWidth,
      y: PLOT.top + (1 - bucket.count / chartMax) * plotHeight,
    }));
    const nextVisiblePoints = nextPoints.filter((point) => !point.is_future);
    const nextLinePath = nextVisiblePoints
      .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`)
      .join(' ');
    const bottom = CHART_HEIGHT - PLOT.bottom;
    const nextAreaPath = nextVisiblePoints.length > 0
      ? `M ${nextVisiblePoints[0].x} ${bottom} ${nextLinePath.replace(/^M/, 'L')} L ${nextVisiblePoints[nextVisiblePoints.length - 1].x} ${bottom} Z`
      : '';
    const tickStep = Math.max(1, Math.ceil(buckets.length / 7));
    const nextTickIndexes = buckets
      .map((_, index) => index)
      .filter((index) => index % tickStep === 0 || index === buckets.length - 1);
    const nextYTicks = Array.from({ length: 4 }, (_, index) => {
      const ratio = index / 3;
      return {
        value: Math.round(chartMax * (1 - ratio)),
        y: PLOT.top + ratio * plotHeight,
      };
    });

    return {
      points: nextPoints,
      visiblePoints: nextVisiblePoints,
      linePath: nextLinePath,
      areaPath: nextAreaPath,
      tickIndexes: nextTickIndexes,
      yTicks: nextYTicks,
    };
  }, [buckets, chartMax]);

  const latestPoint = visiblePoints[visiblePoints.length - 1];

  return (
    <section className="alert-trend-card" aria-labelledby="alert-trend-title">
      <div className="alert-trend-card__header">
        <div>
          <h3 id="alert-trend-title" className="alert-trend-card__title">
            <span className="title-icon"><LineChartOutlined /></span>
            告警趋势
          </h3>
          <p>{PERIOD_HINTS[period]}</p>
        </div>
        <Segmented
          size="small"
          value={period}
          onChange={(value) => setPeriod(value as AlertStatsPeriod)}
          options={PERIOD_OPTIONS}
          aria-label="告警趋势时间范围"
        />
      </div>

      <div className="alert-trend-card__summary" aria-live="polite">
        <div>
          <span>时段告警</span>
          <strong>{totalCount}</strong>
          <small>条</small>
        </div>
        <div>
          <span>单周期峰值</span>
          <strong>{peakCount}</strong>
          <small>条</small>
        </div>
        {totalCount === 0 ? <em>当前时段暂无告警</em> : null}
      </div>

      {loading && !trend ? (
        <div className="alert-trend-card__placeholder" aria-busy="true">正在读取趋势...</div>
      ) : error && !trend ? (
        <div className="alert-trend-card__placeholder is-error">{error}</div>
      ) : (
        <div className={`alert-trend-card__chart-scroll ${loading ? 'is-refreshing' : ''}`}>
          <svg
            className="alert-trend-card__chart"
            viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
            role="img"
            aria-label={`${PERIOD_HINTS[period]}告警趋势，共 ${totalCount} 条`}
          >
            <title>{`${PERIOD_HINTS[period]}告警趋势，共 ${totalCount} 条`}</title>
            <defs>
              <linearGradient id="alertTrendArea" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#46bfa7" stopOpacity="0.3" />
                <stop offset="100%" stopColor="#46bfa7" stopOpacity="0.015" />
              </linearGradient>
            </defs>

            {yTicks.map((tick) => (
              <g key={`${tick.y}-${tick.value}`}>
                <line
                  className="alert-trend-card__grid-line"
                  x1={PLOT.left}
                  x2={CHART_WIDTH - PLOT.right}
                  y1={tick.y}
                  y2={tick.y}
                />
                <text className="alert-trend-card__axis-label" x={PLOT.left - 10} y={tick.y + 4}>
                  {tick.value}
                </text>
              </g>
            ))}

            {areaPath ? <path className="alert-trend-card__area" d={areaPath} /> : null}
            {linePath ? <path className="alert-trend-card__line" d={linePath} /> : null}

            {points.map((point, index) => (
              <g key={point.start} className={point.is_future ? 'is-future' : undefined}>
                {!point.is_future ? (
                  <circle className="alert-trend-card__point" cx={point.x} cy={point.y} r="3.5">
                    <title>{`${point.label} · ${point.count} 条告警`}</title>
                  </circle>
                ) : null}
                {tickIndexes.includes(index) ? (
                  <text
                    className="alert-trend-card__x-label"
                    x={point.x}
                    y={CHART_HEIGHT - 9}
                    textAnchor={index === 0 ? 'start' : index === buckets.length - 1 ? 'end' : 'middle'}
                  >
                    {point.label}
                  </text>
                ) : null}
              </g>
            ))}

            {latestPoint ? (
              <g className="alert-trend-card__live-point">
                <circle cx={latestPoint.x} cy={latestPoint.y} r="9" />
                <circle cx={latestPoint.x} cy={latestPoint.y} r="4" />
              </g>
            ) : null}
          </svg>
        </div>
      )}

      {error && trend ? <span className="alert-trend-card__stale">更新失败，正在显示上次数据</span> : null}
    </section>
  );
};

export default AlertTrendChart;
