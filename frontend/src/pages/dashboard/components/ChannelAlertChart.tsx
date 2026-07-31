import React, { useCallback, useEffect, useState } from 'react';
import { Segmented } from 'antd';
import { BarChartOutlined } from '@ant-design/icons';
import { getChannelAlertStats } from '@/services/api';
import './ChannelAlertChart.css';

type Period = 'day' | 'week' | 'month' | 'year';

interface ChannelStat {
  id: number;
  name: string;
  count: number;
}

const PERIOD_LABELS: Record<Period, string> = {
  day: '日',
  week: '周',
  month: '月',
  year: '年',
};

const ChannelAlertChart: React.FC = () => {
  const [period, setPeriod] = useState<Period>('day');
  const [channels, setChannels] = useState<ChannelStat[]>([]);
  const [loading, setLoading] = useState(true);

  const loadStats = useCallback(async (p: Period) => {
    try {
      setLoading(true);
      const response = await getChannelAlertStats(p);
      setChannels(response?.channels || []);
    } catch (error) {
      console.error('加载通道告警统计失败:', error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadStats(period);
  }, [period, loadStats]);

  const maxCount = Math.max(0, ...channels.map((c) => c.count));
  const totalCount = channels.reduce((sum, c) => sum + c.count, 0);

  return (
    <div className="channel-alert-chart">
      <div className="channel-alert-chart__header">
        <h3 className="channel-alert-chart__title">
          <span className="title-icon">
            <BarChartOutlined />
          </span>
          通道告警统计
        </h3>
        <Segmented
          size="small"
          value={period}
          onChange={(value) => setPeriod(value as Period)}
          options={(['day', 'week', 'month', 'year'] as Period[]).map((p) => ({
            label: PERIOD_LABELS[p],
            value: p,
          }))}
        />
      </div>

      {loading && channels.length === 0 ? (
        <div className="channel-alert-chart__placeholder">加载中...</div>
      ) : channels.length === 0 ? (
        <div className="channel-alert-chart__placeholder">暂无视频通道</div>
      ) : totalCount === 0 ? (
        <div className="channel-alert-chart__placeholder">该时间段内暂无告警</div>
      ) : (
        <div className={`channel-alert-chart__body ${loading ? 'is-refreshing' : ''}`}>
          <div className="channel-alert-chart__bars">
            {channels.map((channel) => {
              const heightPercent = maxCount > 0 ? (channel.count / maxCount) * 100 : 0;
              return (
                <div
                  key={channel.id}
                  className="channel-alert-chart__item"
                  title={`${channel.name}：${channel.count} 条告警`}
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
