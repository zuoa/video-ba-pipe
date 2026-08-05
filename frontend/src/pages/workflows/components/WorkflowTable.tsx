import React, { useDeferredValue, useMemo, useState } from 'react';
import { Table, Space, Tag, Select, Badge, Input, Alert } from 'antd';
import Button from '@/components/common/AppButton';
import AppToolbar from '@/components/common/AppToolbar';
import { useNavigate } from 'umi';
import {
  EditOutlined,
  DeleteOutlined,
  PlayCircleOutlined,
  PauseCircleOutlined,
  ApartmentOutlined,
  FilterOutlined,
  CheckOutlined,
  CloseOutlined,
  CopyOutlined,
  FileTextOutlined,
} from '@ant-design/icons';
import './WorkflowTable.css';

const { Search } = Input;

export interface WorkflowTableProps {
  workflows: any[];
  loading: boolean;
  videoSources: any[];
  onEdit: (workflow: any) => void;
  onDelete: (id: number) => void;
  onOpenEditor: (workflow: any) => void;
  onActivate: (id: number) => void;
  onDeactivate: (id: number) => void;
  onCopy?: (workflow: any) => void;
  onBatchActivate?: (ids: number[]) => void;
  onBatchDeactivate?: (ids: number[]) => void;
  onBatchDelete?: (ids: number[]) => void;
}

const WorkflowTable: React.FC<WorkflowTableProps> = ({
  workflows,
  loading,
  videoSources,
  onEdit,
  onDelete,
  onOpenEditor,
  onActivate,
  onDeactivate,
  onCopy,
  onBatchActivate,
  onBatchDeactivate,
  onBatchDelete,
}) => {
  const navigate = useNavigate();
  const [filterSource, setFilterSource] = useState<number | undefined>();
  const [filterType, setFilterType] = useState<'template' | 'workflow' | undefined>();
  const [filterTemplate, setFilterTemplate] = useState<number | 'direct' | undefined>();
  const [searchText, setSearchText] = useState('');
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const deferredSearchText = useDeferredValue(searchText.trim().toLowerCase());

  const templates = useMemo(
    () => workflows.filter((workflow) => workflow.is_template),
    [workflows],
  );

  const filteredWorkflows = useMemo(() => workflows.filter((workflow) => {
    const workflowData = workflow.workflow_data || {};
    const nodes = workflowData.nodes || [];
    const sourceNode = nodes.find((node: any) => node.type === 'source');
    const workflowSourceId = workflow.video_source_id ?? sourceNode?.dataId;

    const matchSource = filterSource === undefined || Number(workflowSourceId) === filterSource;
    const matchType = filterType === undefined
      || (filterType === 'template' ? workflow.is_template : !workflow.is_template);
    const matchTemplate = filterTemplate === undefined
      || (filterTemplate === 'direct'
        ? !workflow.is_template && workflow.source_template_id == null
        : workflow.source_template_id === filterTemplate);
    const matchSearch = !deferredSearchText
      || workflow.name?.toLowerCase().includes(deferredSearchText)
      || workflow.description?.toLowerCase().includes(deferredSearchText);
    return matchSource && matchType && matchTemplate && matchSearch;
  }), [deferredSearchText, filterSource, filterTemplate, filterType, workflows]);

  const handleBatchActivate = () => {
    if (selectedRowKeys.length === 0) return;
    if (onBatchActivate) {
      onBatchActivate(selectedRowKeys as number[]);
      setSelectedRowKeys([]);
    } else {
      selectedRowKeys.forEach((id) => onActivate(id as number));
      setSelectedRowKeys([]);
    }
  };

  const handleBatchDeactivate = () => {
    if (selectedRowKeys.length === 0) return;
    if (onBatchDeactivate) {
      onBatchDeactivate(selectedRowKeys as number[]);
      setSelectedRowKeys([]);
    } else {
      selectedRowKeys.forEach((id) => onDeactivate(id as number));
      setSelectedRowKeys([]);
    }
  };

  const handleBatchDelete = () => {
    if (selectedRowKeys.length === 0) return;
    if (onBatchDelete) {
      onBatchDelete(selectedRowKeys as number[]);
      setSelectedRowKeys([]);
    } else {
      selectedRowKeys.forEach((id) => onDelete(id as number));
      setSelectedRowKeys([]);
    }
  };

  const columns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 80,
      render: (id: number) => (
        <div className="id-badge">
          <span>{id}</span>
        </div>
      ),
    },
    {
      title: '类型',
      key: 'workflow_type',
      width: 110,
      render: (_: any, record: any) => record.is_template ? (
        <Tag color="purple" icon={<FileTextOutlined />}>模板</Tag>
      ) : (
        <Tag color="blue" icon={<ApartmentOutlined />}>普通编排</Tag>
      ),
    },
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 280,
      render: (name: string, record: any) => (
        <div className="name-cell">
          <div className={`name-icon ${record.is_template ? 'name-icon-template' : ''}`}>
            {record.is_template ? <FileTextOutlined /> : <ApartmentOutlined />}
          </div>
          <div className="name-content">
            <div className="name-text">{name || '未命名'}</div>
            {record.description && (
              <div className="name-desc">{record.description}</div>
            )}
          </div>
        </div>
      ),
    },
    {
      title: '视频源',
      key: 'video_source',
      width: 200,
      render: (_: any, record: any) => {
        // 从 workflow_data 的 nodes 中查找 source 节点
        const workflowData = record.workflow_data || {};
        const nodes = workflowData.nodes || [];

        // 查找类型为 'source' 的节点
        const sourceNode = nodes.find((node: any) => node.type === 'source');

        if (record.is_template) {
          return <Tag color="purple">复制时绑定</Tag>;
        }

        const normalizedSourceId = record.video_source_id ?? sourceNode?.dataId;
        if (normalizedSourceId) {
          const sourceId = normalizedSourceId;
          const source = videoSources.find((s) => String(s.id) === String(sourceId));
          return source ? (
            <Tag color="blue" className="source-tag">
              {source.name}
            </Tag>
          ) : (
            <Tag color="default">未配置</Tag>
          );
        }

        return <Tag color="default">未配置</Tag>;
      },
    },
    {
      title: '来源模板',
      key: 'source_template',
      width: 180,
      render: (_: any, record: any) => {
        if (record.is_template) return <span className="muted-cell">模板本身</span>;
        if (record.source_template_id) {
          return (
            <Tag color="geekblue">
              {record.source_template_name || `模板 #${record.source_template_id}`}
            </Tag>
          );
        }
        return <span className="muted-cell">自主创建</span>;
      },
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 120,
      render: (isActive: boolean, record: any) => record.is_template ? (
        <Badge status="default" text={<span className="status-template">不调度</span>} />
      ) : (
        <Badge
          status={isActive ? 'processing' : 'default'}
          text={
            <span className={isActive ? 'status-active' : 'status-inactive'}>
              {isActive ? '运行中' : '已停用'}
            </span>
          }
        />
      ),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 180,
      render: (date: string) => {
        if (!date) return '-';
        return new Date(date).toLocaleString('zh-CN', {
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
        });
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 320,
      fixed: 'right' as const,
      render: (_: any, record: any) => (
        <Space size="small">
          <Button
            size="small"
            icon={<ApartmentOutlined />}
            onClick={() => navigate(`/workflows/editor/${record.id}`)}
            className="action-btn action-btn-edit"
          >
            编排
          </Button>
          {onCopy && record.is_template && (
            <Button
              size="small"
              icon={<CopyOutlined />}
              onClick={() => onCopy(record)}
              className="action-btn"
            >
              复制
            </Button>
          )}
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => onEdit(record)}
            className="action-btn"
          >
            编辑
          </Button>
          {!record.is_template && (record.is_active ? (
            <Button
              size="small"
              icon={<PauseCircleOutlined />}
              onClick={() => onDeactivate(record.id)}
              className="action-btn action-btn-warning"
            >
              停用
            </Button>
          ) : (
            <Button
              size="small"
              type="primary"
              icon={<PlayCircleOutlined />}
              onClick={() => onActivate(record.id)}
              className="action-btn"
            >
              激活
            </Button>
          ))}
          <Button
            size="small"
            icon={<DeleteOutlined />}
            onClick={() => onDelete(record.id)}
            className="action-btn action-btn-delete"
          >
            删除
          </Button>
        </Space>
      ),
    },
  ];

  const rowSelection = {
    selectedRowKeys,
    onChange: (newSelectedRowKeys: React.Key[]) => {
      setSelectedRowKeys(newSelectedRowKeys);
    },
    getCheckboxProps: (record: any) => ({
      disabled: record.is_template,
      name: record.is_template ? '编排模板不参与批量运行操作' : record.name,
    }),
  };

  return (
    <div className="workflow-table-wrapper">
      {/* 筛选栏 */}
      <AppToolbar
        className="filter-bar"
        actions={(
          <Search
            placeholder="搜索名称或描述"
            allowClear
            className="workflow-search"
            onChange={(e) => setSearchText(e.target.value)}
          />
        )}
      >
        <div className="filter-left">
          <Space size="middle">
            <div className="filter-item">
              <span className="filter-label">类型</span>
              <Select
                placeholder="全部类型"
                allowClear
                style={{ width: 140 }}
                value={filterType}
                onChange={setFilterType}
                options={[
                  { label: '编排模板', value: 'template' },
                  { label: '普通编排', value: 'workflow' },
                ]}
              />
            </div>
            <div className="filter-item">
              <span className="filter-label">来源模板</span>
              <Select
                placeholder="全部来源"
                allowClear
                style={{ width: 200 }}
                value={filterTemplate}
                onChange={setFilterTemplate}
              >
                <Select.Option value="direct">自主创建</Select.Option>
                {templates.map((template) => (
                  <Select.Option key={template.id} value={template.id}>
                    {template.name}
                  </Select.Option>
                ))}
              </Select>
            </div>
            <div className="filter-item">
              <FilterOutlined className="filter-icon" />
              <span className="filter-label">按视频源筛选</span>
              <Select
                placeholder="全部视频源"
                allowClear
                style={{ width: 200 }}
                value={filterSource}
                onChange={setFilterSource}
              >
                {videoSources.map((source) => (
                  <Select.Option key={source.id} value={source.id}>
                    {source.name}
                  </Select.Option>
                ))}
              </Select>
            </div>
            {(filterSource !== undefined || filterType !== undefined || filterTemplate !== undefined) && (
              <div className="filter-info">
                显示 <span className="filter-count">{filteredWorkflows.length}</span> 个算法编排
              </div>
            )}
          </Space>
        </div>
      </AppToolbar>

      {/* 批量操作栏 */}
      {selectedRowKeys.length > 0 && (
        <Alert
          message={
            <Space size="large">
              <span>
                已选择 <strong>{selectedRowKeys.length}</strong> 个工作流
              </span>
              <Space size="small">
                <Button
                  size="small"
                  type="primary"
                  icon={<PlayCircleOutlined />}
                  onClick={handleBatchActivate}
                >
                  批量激活
                </Button>
                <Button
                  size="small"
                  icon={<PauseCircleOutlined />}
                  onClick={handleBatchDeactivate}
                >
                  批量停用
                </Button>
                <Button
                  size="small"
                  danger
                  icon={<DeleteOutlined />}
                  onClick={handleBatchDelete}
                >
                  批量删除
                </Button>
                <Button
                  size="small"
                  icon={<CloseOutlined />}
                  onClick={() => setSelectedRowKeys([])}
                >
                  取消选择
                </Button>
              </Space>
            </Space>
          }
          type="info"
          showIcon
          className="batch-actions-alert"
        />
      )}

      {/* 表格 */}
      <Table
        dataSource={filteredWorkflows}
        columns={columns}
        rowKey="id"
        loading={loading}
        rowSelection={rowSelection}
        rowClassName={(record) => record.is_template ? 'workflow-template-row' : ''}
        pagination={{
          pageSize: 10,
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (total) => `共 ${total} 条`,
        }}
        className="workflow-table"
      />
    </div>
  );
};

export default WorkflowTable;
