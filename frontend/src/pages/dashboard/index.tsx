import { useEffect, useState } from 'react';
import { Row, Col } from 'antd';
import {
  AppstoreOutlined,
  AlertOutlined,
  RocketOutlined,
  HistoryOutlined,
  ArrowUpOutlined,
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  ExperimentOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons';
import { getVideoSources, getAlgorithms, getTodayAlertsCount, getAlerts } from '@/services/api';
import StatCard from './components/StatCard';
import QuickAccessCard from './components/QuickAccessCard';
import RecentAlertCard from './components/RecentAlertCard';
import WelcomeBanner from './components/WelcomeBanner';
import type { Alert as AlertType, Task as TaskType } from './components/RecentAlertCard';
import './index.css';

export default function Dashboard() {
  const [stats, setStats] = useState({
    totalTasks: 0,
    runningTasks: 0,
    totalAlgorithms: 0,
    todayAlerts: 0,
  });
  const [alerts, setAlerts] = useState<AlertType[]>([]);
  const [tasks, setTasks] = useState<TaskType[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadDashboardData();

    // 自动刷新（每30秒）
    const interval = setInterval(loadDashboardData, 30000);

    return () => clearInterval(interval);
  }, []);

  const loadDashboardData = async () => {
    try {
      setLoading(true);

      // 并行加载数据
      const [sources, algorithms, alertsResponse, todayAlertsResponse] =
        await Promise.all([
          getVideoSources(),
          getAlgorithms(),
          getAlerts({ page: 1, per_page: 5 }),
          getTodayAlertsCount(),
        ]);

      const runningTasksCount = sources?.filter((t: any) => t.status === 'RUNNING').length || 0;

      setStats({
        totalTasks: sources?.length || 0,
        runningTasks: runningTasksCount,
        totalAlgorithms: algorithms?.length || 0,
        todayAlerts: todayAlertsResponse?.count || 0,
      });

      setTasks(sources || []);
      setAlerts(alertsResponse?.data || []);
    } catch (error) {
      console.error('加载仪表盘数据失败:', error);
    } finally {
      setLoading(false);
    }
  };

  const quickAccessItems = [
    {
      title: '视频源管理',
      description: '新增、启停并查看视频源',
      icon: <VideoCameraOutlined />,
      iconBgColor: '#000000',
      path: '/video-sources',
    },
    {
      title: '算法管理',
      description: '维护检测算法与模型版本',
      icon: <ExperimentOutlined />,
      iconBgColor: '#000000',
      path: '/algorithms',
    },
    {
      title: '告警记录',
      description: '按时间和视频源追踪最新告警',
      icon: <AlertOutlined />,
      iconBgColor: '#ff4d4f',
      path: '/alerts',
    },
  ];

  return (
    <div className="dashboard-page">
      {/* 统计卡片 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            icon={<AppstoreOutlined />}
            title="视频源总数"
            value={stats.totalTasks}
            subtitle={`当前有 ${stats.runningTasks} 路视频源正在运行`}
            iconBgColor="#000000"
            trendIcon={<ArrowUpOutlined />}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            icon={<ExperimentOutlined />}
            title="算法模型"
            value={stats.totalAlgorithms}
            subtitle="已接入的算法与模型总数"
            iconBgColor="#000000"
            trendIcon={<CheckCircleOutlined />}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            icon={<AlertOutlined />}
            title="今日告警"
            value={stats.todayAlerts}
            subtitle="今日累计触发的告警次数"
            iconBgColor="#ff4d4f"
            trendIcon={<ExclamationCircleOutlined />}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            icon={<CheckCircleOutlined />}
            title="系统状态"
            value="运行中"
            subtitle="服务、视频流处理与告警链路正常"
            iconBgColor="#52c41a"
            trendIcon={<CheckCircleOutlined />}
          />
        </Col>
      </Row>

      {/* 快捷操作和最近告警 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} lg={12}>
          <QuickAccessCard
            title="常用入口"
            icon={<RocketOutlined />}
            items={quickAccessItems}
          />
        </Col>
        <Col xs={24} lg={12}>
          <RecentAlertCard
            title="最新告警"
            icon={<HistoryOutlined />}
            alerts={alerts}
            tasks={tasks}
            viewAllPath="/alerts"
            loading={loading}
          />
        </Col>
      </Row>

      {/* 欢迎横幅 */}
      <WelcomeBanner />
    </div>
  );
}
