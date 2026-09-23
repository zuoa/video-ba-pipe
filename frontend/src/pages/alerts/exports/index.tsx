import { tr, trf } from '@/i18n/tr';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { history } from '@umijs/max';
import { Progress, Space, Table, Typography, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  ArrowLeftOutlined,
  DeleteOutlined,
  DownloadOutlined,
  ExportOutlined,
  StopOutlined,
} from '@ant-design/icons';
import Button from '@/components/common/AppButton';
import { PageHeader, StatusBadge, useAppConfirm } from '@/components/common';
import type { SemanticTone } from '@/components/common/AppButton';
import {
  cancelAlertExport,
  deleteAlertExport,
  downloadAlertExport,
  getAlertExports,
  getApiErrorMessage,
  type AlertExportTask,
} from '@/services/api';
import './index.css';

const STATUS_META: Record<string, { label: string; tone: SemanticTone | 'muted' }> = {
  pending: { label: tr("排队中"), tone: 'info' },
  running: { label: tr("进行中"), tone: 'info' },
  succeeded: { label: tr("已完成"), tone: 'success' },
  failed: { label: tr("失败"), tone: 'danger' },
  cancelled: { label: tr("已取消"), tone: 'muted' },
};

function formatDateTime(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function formatFileSize(bytes?: number | null) {
  if (!bytes && bytes !== 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

const AlertExportsPage: React.FC = () => {
  const [tasks, setTasks] = useState<AlertExportTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [downloadingId, setDownloadingId] = useState<number | null>(null);
  const [pagination, setPagination] = useState({
    page: 1,
    per_page: 20,
    total: 0,
  });
  const confirmAction = useAppConfirm();

  const hasActiveTask = useMemo(
    () => tasks.some((task) => task.status === 'pending' || task.status === 'running'),
    [tasks],
  );

  const loadTasks = useCallback(async (silent = false) => {
    if (!silent) {
      setLoading(true);
    }
    try {
      const response = await getAlertExports({
        page: pagination.page,
        per_page: pagination.per_page,
      });
      setTasks(response.data || []);
      setPagination((prev) => ({
        ...prev,
        total: response.pagination?.total || 0,
        page: response.pagination?.page || prev.page,
        per_page: response.pagination?.per_page || prev.per_page,
      }));
    } catch (error: any) {
      if (!silent) {
        message.error(getApiErrorMessage(error, tr("加载导出任务失败")));
      }
    } finally {
      if (!silent) {
        setLoading(false);
      }
    }
  }, [pagination.page, pagination.per_page]);

  useEffect(() => {
    loadTasks();
  }, [loadTasks]);

  useEffect(() => {
    if (!hasActiveTask) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      loadTasks(true);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [hasActiveTask, loadTasks]);

  const handleDownload = async (task: AlertExportTask) => {
    setDownloadingId(task.id);
    try {
      await downloadAlertExport(task.id, task.file_url);
    } catch (error: any) {
      message.error(getApiErrorMessage(error, tr("下载失败")));
    } finally {
      setDownloadingId(null);
    }
  };

  const handleCancel = (task: AlertExportTask) => {
    confirmAction({
      tone: 'danger',
      title: tr("取消导出"),
      objectName: trf("任务 #__VAR0__", [task.id]),
      description: tr("取消后将停止打包并删除已生成的临时文件。"),
      confirmText: tr("确认取消"),
      onConfirm: async () => {
        try {
          await cancelAlertExport(task.id);
          message.success(tr("已取消导出"));
          loadTasks();
        } catch (error: any) {
          message.error(getApiErrorMessage(error, tr("取消失败")));
        }
      },
    });
  };

  const handleDelete = (task: AlertExportTask) => {
    confirmAction({
      tone: 'danger',
      title: tr("删除导出"),
      objectName: task.file_name || trf("任务 #__VAR0__", [task.id]),
      description: tr("删除后将无法再下载该 ZIP 文件。"),
      onConfirm: async () => {
        try {
          await deleteAlertExport(task.id);
          message.success(tr("已删除"));
          loadTasks();
        } catch (error: any) {
          message.error(getApiErrorMessage(error, tr("删除失败")));
        }
      },
    });
  };

  const columns: ColumnsType<AlertExportTask> = [
    {
      title: tr("创建时间"),
      dataIndex: 'created_at',
      width: 180,
      render: formatDateTime,
    },
    {
      title: tr("筛选条件"),
      dataIndex: 'filter_summary',
      ellipsis: true,
      render: (value: string, record) => (
        <div className="alert-exports__summary">
          <span>{value || tr("全部告警")}</span>
          {record.error_message && record.status === 'failed' ? (
            <Typography.Text type="danger" ellipsis={{ tooltip: record.error_message }}>
              {record.error_message}
            </Typography.Text>
          ) : null}
        </div>
      ),
    },
    {
      title: tr("记录数"),
      dataIndex: 'total_count',
      width: 90,
    },
    {
      title: tr("进度"),
      width: 180,
      render: (_: unknown, record) => (
        <div className="alert-exports__progress">
          <Progress
            percent={record.progress_percent || 0}
            size="small"
            status={
              record.status === 'failed'
                ? 'exception'
                : record.status === 'succeeded'
                  ? 'success'
                  : record.status === 'cancelled'
                    ? 'normal'
                    : 'active'
            }
          />
          <span className="alert-exports__progressMeta">
            {record.processed_count}/{record.total_count}
            {record.missing_image_count ? trf(" · 缺图 __VAR0__", [record.missing_image_count]) : ''}
          </span>
        </div>
      ),
    },
    {
      title: tr("状态"),
      dataIndex: 'status',
      width: 110,
      render: (status: string) => {
        const meta = STATUS_META[status] || { label: status, tone: 'muted' as const };
        return (
          <StatusBadge
            status={status.toUpperCase()}
            text={meta.label}
            tone={meta.tone}
          />
        );
      },
    },
    {
      title: tr("文件大小"),
      dataIndex: 'file_size',
      width: 110,
      render: formatFileSize,
    },
    {
      title: tr("过期时间"),
      dataIndex: 'expires_at',
      width: 180,
      render: formatDateTime,
    },
    {
      title: tr("操作"),
      width: 220,
      render: (_: unknown, record) => {
        const active = record.status === 'pending' || record.status === 'running';
        return (
          <Space size={4}>
            <Button
              type="link"
              icon={<DownloadOutlined />}
              disabled={!record.downloadable}
              loading={downloadingId === record.id}
              onClick={() => handleDownload(record)}
            >
              {tr("下载")}
            </Button>
            {active ? (
              <Button
                type="link"
                danger
                icon={<StopOutlined />}
                onClick={() => handleCancel(record)}
              >
                {tr("取消")}
              </Button>
            ) : (
              <Button
                type="link"
                danger
                icon={<DeleteOutlined />}
                onClick={() => handleDelete(record)}
              >
                {tr("删除")}
              </Button>
            )}
          </Space>
        );
      },
    },
  ];

  return (
    <div className="alert-exports-page">
      <PageHeader
        icon={<ExportOutlined />}
        eyebrow="EXPORT JOBS"
        title={tr("导出管理")}
        subtitle={tr("查看告警导出进度，完成后下载 ZIP 包")}
        count={pagination.total}
        countLabel={tr("个任务")}
        extra={(
          <Button icon={<ArrowLeftOutlined />} onClick={() => history.push('/alerts')}>
            {tr("返回告警记录")}
          </Button>
        )}
      />

      <Table
        className="alert-exports__table"
        rowKey="id"
        columns={columns}
        dataSource={tasks}
        loading={loading}
        pagination={{
          current: pagination.page,
          pageSize: pagination.per_page,
          total: pagination.total,
          showSizeChanger: true,
          onChange: (page, pageSize) => {
            setPagination((prev) => ({
              ...prev,
              page,
              per_page: pageSize || prev.per_page,
            }));
          },
        }}
      />
    </div>
  );
};

export default AlertExportsPage;
