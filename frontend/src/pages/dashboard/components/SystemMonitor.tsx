import React from 'react';
import {
  CloudServerOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  HddOutlined,
} from '@ant-design/icons';
import './SystemMonitor.css';

export interface CpuMetrics {
  usage_percent: number;
  logical_cores: number;
  physical_cores: number;
  frequency_mhz?: number | null;
  load_average?: number[] | null;
}

export interface MemoryMetrics {
  total_bytes: number;
  used_bytes: number;
  available_bytes: number;
  usage_percent: number;
  swap_total_bytes: number;
  swap_used_bytes: number;
  swap_usage_percent: number;
}

export interface DiskMetrics {
  device: string;
  mountpoint: string;
  filesystem: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  usage_percent: number;
}

export interface NetworkMetrics {
  bytes_sent: number;
  bytes_received: number;
  upload_bytes_per_second: number;
  download_bytes_per_second: number;
  active_interfaces: string[];
  scope?: 'host' | 'container';
  rate_sampled?: boolean;
}

export interface GpuMetrics {
  index: number;
  name: string;
  vendor: string;
  usage_percent?: number | null;
  memory_total_bytes?: number | null;
  memory_used_bytes?: number | null;
  memory_usage_percent?: number | null;
  temperature_c?: number | null;
  power_watts?: number | null;
}

export interface NpuMetrics {
  index: number;
  name: string;
  vendor: string;
  usage_percent?: number | null;
  core_load_percent?: number[] | null;
  memory_total_bytes?: number | null;
  memory_used_bytes?: number | null;
  memory_usage_percent?: number | null;
  temperature_c?: number | null;
  power_watts?: number | null;
}

export interface SystemMetrics {
  timestamp: number;
  hostname: string;
  platform: string;
  uptime_seconds: number;
  cpu: CpuMetrics;
  memory: MemoryMetrics;
  disks: DiskMetrics[];
  network: NetworkMetrics;
  gpus: GpuMetrics[];
  npus: NpuMetrics[];
}

interface SystemMonitorProps {
  metrics: SystemMetrics | null;
  loading: boolean;
  error?: string;
}

interface UsageBarProps {
  value?: number | null;
  label: string;
}

const formatBytes = (bytes?: number | null, decimals = 1) => {
  if (bytes === undefined || bytes === null) return '不可用';
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  const unitIndex = Math.min(
    Math.floor(Math.log(Math.abs(bytes)) / Math.log(1024)),
    units.length - 1,
  );
  return `${(bytes / 1024 ** unitIndex).toFixed(decimals)} ${units[unitIndex]}`;
};

const formatRate = (bytesPerSecond: number) => `${formatBytes(bytesPerSecond)}/s`;

const formatUptime = (seconds: number) => {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days} 天 ${hours} 小时`;
  if (hours > 0) return `${hours} 小时 ${minutes} 分钟`;
  return `${minutes} 分钟`;
};

const getUsageLevel = (value: number) => {
  if (value >= 90) return 'critical';
  if (value >= 75) return 'warning';
  return 'normal';
};

const UsageBar: React.FC<UsageBarProps> = ({ value, label }) => {
  const safeValue = Math.min(Math.max(value ?? 0, 0), 100);
  const unavailable = value === undefined || value === null;
  return (
    <div
      className={`system-usage-bar system-usage-bar--${getUsageLevel(safeValue)}`}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={unavailable ? undefined : safeValue}
      aria-valuetext={unavailable ? '指标不可用' : `${safeValue}%`}
    >
      <span style={{ width: unavailable ? '0%' : `${safeValue}%` }} />
    </div>
  );
};

const MetricCardHeader: React.FC<{
  icon: React.ReactNode;
  title: string;
  value: string;
}> = ({ icon, title, value }) => (
  <div className="system-metric-card__header">
    <span className="system-metric-card__icon">{icon}</span>
    <span className="system-metric-card__title">{title}</span>
    <strong className="system-metric-card__value">{value}</strong>
  </div>
);

const SystemMonitor: React.FC<SystemMonitorProps> = ({ metrics, loading, error }) => {
  if (loading && !metrics) {
    return (
      <section className="system-monitor system-monitor--loading" aria-busy="true">
        <div className="system-monitor__loading-line" />
        <div className="system-monitor__loading-grid">
          {[0, 1, 2, 3, 4].map((item) => (
            <div key={item} className="system-monitor__loading-card" />
          ))}
        </div>
      </section>
    );
  }

  if (!metrics) {
    return (
      <section className="system-monitor system-monitor--error">
        <DashboardOutlined />
        <div>
          <h2>主机状态暂不可用</h2>
          <p>{error || '无法读取系统指标，请稍后刷新页面。'}</p>
        </div>
      </section>
    );
  }

  const cpuSubtitle = [
    `${metrics.cpu.physical_cores || metrics.cpu.logical_cores} 核`,
    metrics.cpu.frequency_mhz ? `${Math.round(metrics.cpu.frequency_mhz)} MHz` : null,
  ].filter(Boolean).join(' · ');
  const primaryDisk = metrics.disks[0];
  const interfaces = metrics.network.active_interfaces;
  const networkScopeLabel = metrics.network.scope === 'host'
    ? '宿主网络'
    : metrics.network.scope === 'container'
      ? 'API 容器网络'
      : '网络';

  return (
    <section className="system-monitor" aria-labelledby="system-monitor-title">
      <div className="system-monitor__header">
        <div>
          <div className="system-monitor__eyebrow">
            <span className={`system-monitor__live-dot ${error ? 'is-stale' : ''}`} />
            {error ? '数据更新中断' : '实时状态'}
          </div>
          <h2 id="system-monitor-title">主机资源</h2>
          <p>
            <span>{metrics.hostname}</span>
            <span>已运行 {formatUptime(metrics.uptime_seconds)}</span>
          </p>
        </div>
        <time dateTime={new Date(metrics.timestamp * 1000).toISOString()}>
          更新于 {new Date(metrics.timestamp * 1000).toLocaleTimeString('zh-CN', { hour12: false })}
        </time>
      </div>

      <div
        className="system-monitor__grid"
        style={{ '--system-metric-count': 4 + metrics.gpus.length + metrics.npus.length } as React.CSSProperties}
      >
        <article className="system-metric-card">
          <MetricCardHeader
            icon={<DashboardOutlined />}
            title="CPU"
            value={`${metrics.cpu.usage_percent.toFixed(1)}%`}
          />
          <UsageBar value={metrics.cpu.usage_percent} label="CPU 使用率" />
          <div className="system-metric-card__details">
            <span>{cpuSubtitle}</span>
            <span>
              {metrics.cpu.load_average?.length
                ? `负载 ${metrics.cpu.load_average.slice(0, 3).join(' / ')}`
                : `${metrics.cpu.logical_cores} 逻辑核心`}
            </span>
          </div>
        </article>

        <article className="system-metric-card">
          <MetricCardHeader
            icon={<DatabaseOutlined />}
            title="内存"
            value={`${metrics.memory.usage_percent.toFixed(1)}%`}
          />
          <UsageBar value={metrics.memory.usage_percent} label="内存使用率" />
          <div className="system-metric-card__details">
            <span>
              {formatBytes(metrics.memory.used_bytes)} / {formatBytes(metrics.memory.total_bytes)}
            </span>
            <span>可用 {formatBytes(metrics.memory.available_bytes)}</span>
          </div>
        </article>

        <article className="system-metric-card">
          <MetricCardHeader
            icon={<HddOutlined />}
            title="磁盘"
            value={primaryDisk ? `${primaryDisk.usage_percent.toFixed(1)}%` : '不可用'}
          />
          <UsageBar value={primaryDisk?.usage_percent} label="主磁盘使用率" />
          <div className="system-metric-card__details">
            {primaryDisk ? (
              <>
                <span>
                  {formatBytes(primaryDisk.used_bytes)} / {formatBytes(primaryDisk.total_bytes)}
                </span>
                <span title={primaryDisk.mountpoint}>挂载于 {primaryDisk.mountpoint}</span>
              </>
            ) : (
              <span>未读取到可用磁盘</span>
            )}
          </div>
        </article>

        <article className="system-metric-card system-metric-card--network">
          <MetricCardHeader
            icon={<CloudServerOutlined />}
            title={networkScopeLabel}
            value={`${interfaces.length} 个接口`}
          />
          <div className="system-network-rates">
            <div>
              <span className="system-network-rates__arrow">↓</span>
              <div>
                <small>下载</small>
                <strong>
                  {metrics.network.rate_sampled === false
                    ? '采样中…'
                    : formatRate(metrics.network.download_bytes_per_second)}
                </strong>
              </div>
            </div>
            <div>
              <span className="system-network-rates__arrow">↑</span>
              <div>
                <small>上传</small>
                <strong>
                  {metrics.network.rate_sampled === false
                    ? '采样中…'
                    : formatRate(metrics.network.upload_bytes_per_second)}
                </strong>
              </div>
            </div>
          </div>
          <div className="system-metric-card__details">
            <span title={interfaces.join(', ')}>
              {interfaces.length ? interfaces.join(' · ') : '无活动网络接口'}
            </span>
          </div>
        </article>

        {metrics.gpus.map((gpu) => (
          <article key={`${gpu.vendor}-${gpu.index}-${gpu.name}`} className="system-gpu-card">
            <div className="system-gpu-card__heading">
              <div>
                <small>{gpu.vendor} · GPU {gpu.index}</small>
                <h3>{gpu.name}</h3>
              </div>
              <strong>{gpu.usage_percent === null || gpu.usage_percent === undefined
                ? '在线'
                : `${gpu.usage_percent.toFixed(1)}%`}</strong>
            </div>
            <UsageBar value={gpu.usage_percent} label={`${gpu.name} GPU 使用率`} />
            <div className="system-gpu-card__stats">
              {gpu.memory_total_bytes ? (
                <span>
                  <small>显存</small>
                  {formatBytes(gpu.memory_used_bytes)} / {formatBytes(gpu.memory_total_bytes)}
                </span>
              ) : null}
              {gpu.temperature_c !== null && gpu.temperature_c !== undefined ? (
                <span>
                  <small>温度</small>
                  {gpu.temperature_c.toFixed(1)}°C
                </span>
              ) : null}
              {gpu.power_watts !== null && gpu.power_watts !== undefined ? (
                <span>
                  <small>功耗</small>
                  {gpu.power_watts.toFixed(1)} W
                </span>
              ) : null}
            </div>
          </article>
        ))}

        {metrics.npus.map((npu) => (
          <article key={`${npu.vendor}-npu-${npu.index}-${npu.name}`} className="system-gpu-card">
            <div className="system-gpu-card__heading">
              <div>
                <small>{npu.vendor} · NPU {npu.index}</small>
                <h3>{npu.name}</h3>
              </div>
              <strong>{npu.usage_percent === null || npu.usage_percent === undefined
                ? '在线'
                : `${npu.usage_percent.toFixed(1)}%`}</strong>
            </div>
            <UsageBar value={npu.usage_percent} label={`${npu.name} NPU 使用率`} />
            <div className="system-gpu-card__stats">
              {npu.core_load_percent && npu.core_load_percent.length ? (
                <span>
                  <small>各核负载</small>
                  {npu.core_load_percent.map((c) => `${c.toFixed(0)}%`).join(' / ')}
                </span>
              ) : null}
              {npu.memory_total_bytes ? (
                <span>
                  <small>显存</small>
                  {formatBytes(npu.memory_used_bytes)} / {formatBytes(npu.memory_total_bytes)}
                </span>
              ) : null}
              {npu.temperature_c !== null && npu.temperature_c !== undefined ? (
                <span>
                  <small>温度</small>
                  {npu.temperature_c.toFixed(1)}°C
                </span>
              ) : null}
            </div>
          </article>
        ))}
      </div>

      {metrics.disks.length > 1 ? (
        <div className="system-monitor__disks">
          <span>其他磁盘</span>
          {metrics.disks.slice(1).map((disk) => (
            <div key={`${disk.device}-${disk.mountpoint}`} className="system-disk-chip">
              <span title={disk.device}>{disk.mountpoint}</span>
              <strong>{disk.usage_percent.toFixed(1)}%</strong>
              <UsageBar value={disk.usage_percent} label={`${disk.mountpoint} 磁盘使用率`} />
              <small>{formatBytes(disk.free_bytes)} 可用</small>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
};

export default React.memo(SystemMonitor);
