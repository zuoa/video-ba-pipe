import { tr, trf } from '@/i18n/tr';
import React, { useEffect, useState } from 'react';
import { Segmented } from 'antd';
import { BarChartOutlined } from '@ant-design/icons';
import { getChannelAlertStats } from '@/services/api';
import type { AlertStatsPeriod } from '@/services/api';
import './ChannelAlertChart.css';

interface ChannelStat {
  id: number;
  name: string;
  count: number;
}

const PERIOD_LABELS: Record<AlertStatsPeriod, string> = {
  hour: tr("时"),
  day: tr("日"),
  week: tr("周"),
  month: tr("月"),
  year: tr("年"),
};

const ChannelAlertChart: React.FC = () => {
  const [period, setPeriod] = useState<AlertStatsPeriod>('day');
  const [channels, setChannels] = useState<ChannelStat[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;

    const loadStats = async () => {
      try {
        setLoading(true);
        const response = await getChannelAlertStats(period);
        if (active) setChannels(response?.channels || []);
      } catch (error) {
        console.error('加载通道告警统计失败:', error);
      } finally {
        if (active) setLoading(false);
      }
    };

    loadStats();
    const refreshTimer = window.setInterval(loadStats, 30_000);
    return () => {
      active = false;
      window.clearInterval(refreshTimer);
    };
  }, [period]);

  const maxCount = Math.max(0, ...channels.map((c) => c.count));
  const totalCount = channels.reduce((sum, c) => sum + c.count, 0);

  return (
    <div className="channel-alert-chart">
      <div className="channel-alert-chart__header">
        <h3 className="channel-alert-chart__title">
          <span className="title-icon">
            <BarChartOutlined />
          </span>
          {tr("通道告警统计")}
        </h3>
        <Segmented
          size="small"
          value={period}
          onChange={(value) => setPeriod(value as AlertStatsPeriod)}
          options={(['hour', 'day', 'week', 'month', 'year'] as AlertStatsPeriod[]).map((p) => ({
            label: PERIOD_LABELS[p],
            value: p,
          }))}
        />
      </div>

      {loading && channels.length === 0 ? (
        <div className="channel-alert-chart__placeholder">{tr("加载中...")}</div>
      ) : channels.length === 0 ? (
        <div className="channel-alert-chart__placeholder">{tr("暂无视频通道")}</div>
      ) : totalCount === 0 ? (
        <div className="channel-alert-chart__placeholder">{tr("该时间段内暂无告警")}</div>
      ) : (
        <div className={`channel-alert-chart__body ${loading ? 'is-refreshing' : ''}`}>
          <div className="channel-alert-chart__bars">
            {channels.map((channel) => {
              const heightPercent = maxCount > 0 ? (channel.count / maxCount) * 100 : 0;
              return (
                <div
                  key={channel.id}
                  className="channel-alert-chart__item"
                  title={trf("__VAR0__：__VAR1__ 条告警", [channel.name, channel.count])}
                >
                  <span className="channel-alert-chart__count">{channel.count}</span>
                  <div className="channel-alert-chart__bar-track">
                    <div
                      className={`channel-alert-chart__bar ${channel.count === 0 ? 'is-empty' : ''}`}
                      style={{ height: `${Math.max(heightPercent, channel.count > 0 ? 4 : 0)}%` }}
                    />
                  </div>
                  <span className="channel-alert-chart__name">{channel.name}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};

export default ChannelAlertChart;
