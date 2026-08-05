import React, { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { Badge, Drawer, Input, Select, Space, Table, Tag } from 'antd';
import Button from '@/components/common/AppButton';
import AppToolbar from '@/components/common/AppToolbar';
import { useNavigate } from 'umi';
import {
  EditOutlined,
  DeleteOutlined,
  PlayCircleOutlined,
  PauseCircleOutlined,
  ApartmentOutlined,
  CloseOutlined,
  CopyOutlined,
  FileTextOutlined,
  ControlOutlined,
  MenuUnfoldOutlined,
} from '@ant-design/icons';
import type { Workflow } from '@/services/api';
import WorkflowSourceTree, { type WorkflowTreeKey } from './WorkflowSourceTree';
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
  onBatchConfig?: (workflows: Workflow[]) => void;
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
  onBatchConfig,
}) => {
  const navigate = useNavigate();
  const [treeKey, setTreeKey] = useState<WorkflowTreeKey>('all');
  const [filterTemplate, setFilterTemplate] = useState<number | 'direct' | undefined>();
  const [searchText, setSearchText] = useState('');
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [sourceDrawerOpen, setSourceDrawerOpen] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const deferredSearchText = useDeferredValue(searchText.trim().toLowerCase());

  const templates = useMemo(
    () => workflows.filter((workflow) => workflow.is_template),
    [workflows],
  );

  const toolbarFilteredWorkflows = useMemo(() => workflows.filter((workflow) => {
    const matchTemplate = filterTemplate === undefined
      || (filterTemplate === 'direct'
        ? !workflow.is_template && workflow.source_template_id == null
        : workflow.source_template_id === filterTemplate);
    const matchSearch = !deferredSearchText
      || workflow.name?.toLowerCase().includes(deferredSearchText)
      || workflow.description?.toLowerCase().includes(deferredSearchText);
    return matchTemplate && matchSearch;
  }), [deferredSearchText, filterTemplate, workflows]);

  const filteredWorkflows = useMemo(() => toolbarFilteredWorkflows.filter((workflow) => {
    if (treeKey === 'all') return true;
    if (treeKey === 'templates') return workflow.is_template;
    const workflowData = workflow.workflow_data || {};
    const sourceNode = workflowData.nodes?.find((node: any) => node.type === 'source');
    const sourceId = workflow.video_source_id ?? sourceNode?.dataId;
    if (treeKey === 'unbound') return !workflow.is_template && (sourceId == null || sourceId === '');
    return !workflow.is_template && Number(sourceId) === Number(treeKey.split(':')[1]);
  }), [toolbarFilteredWorkflows, treeKey]);

  const selectedWorkflows = useMemo(() => {
    const selectedIds = new Set(selectedRowKeys.map(Number));
    return workflows.filter((workflow) => selectedIds.has(workflow.id) && !workflow.is_template);
  }, [selectedRowKeys, workflows]);

  const selectableFilteredIds = useMemo(
    () => filteredWorkflows.filter((workflow) => !workflow.is_template).map((workflow) => workflow.id),
    [filteredWorkflows],
  );

  useEffect(() => {
    setSelectedRowKeys([]);
    setCurrentPage(1);
  }, [deferredSearchText, filterTemplate, treeKey]);

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

  const handleTreeSelect = (key: WorkflowTreeKey) => {
    setTreeKey(key);
    setSourceDrawerOpen(false);
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
    preserveSelectedRowKeys: true,
    onChange: (newSelectedRowKeys: React.Key[]) => {
      setSelectedRowKeys(newSelectedRowKeys);
    },
    getCheckboxProps: (record: any) => ({
      disabled: record.is_template,
      name: record.is_template ? '编排模板不参与批量运行操作' : record.name,
    }),
  };

  const tree = (
    <WorkflowSourceTree
      workflows={toolbarFilteredWorkflows}
      videoSources={videoSources}
      selectedKey={treeKey}
      onSelect={handleTreeSelect}
    />
  );

  return (
    <div className="workflow-browser">
      <aside className="workflow-browser__tree">{tree}</aside>
      <section className="workflow-table-wrapper">
        <AppToolbar
          className="filter-bar"
          summary={<span className="filter-info">显示 <span className="filter-count">{filteredWorkflows.length}</span> 个算法编排</span>}
          actions={(
            <Search
              placeholder="搜索名称或描述"
              aria-label="搜索算法编排"
              allowClear
              className="workflow-search"
              value={searchText}
              onChange={(event) => setSearchText(event.target.value)}
            />
          )}
        >
          <Button className="workflow-tree-trigger" icon={<MenuUnfoldOutlined />} onClick={() => setSourceDrawerOpen(true)}>视频源</Button>
          <div className="filter-item">
            <span className="filter-label">来源模板</span>
            <Select
              aria-label="按来源模板筛选"
              placeholder="全部来源"
              allowClear
              className="workflow-template-filter"
              value={filterTemplate}
              onChange={setFilterTemplate}
            >
              <Select.Option value="direct">自主创建</Select.Option>
              {templates.map((template) => (
                <Select.Option key={template.id} value={template.id}>{template.name}</Select.Option>
              ))}
            </Select>
          </div>
        </AppToolbar>

        {selectedRowKeys.length > 0 ? (
          <div className="batch-action-bar" role="region" aria-label="批量操作">
            <div className="batch-action-bar__selection">
              <span>已选择 <strong>{selectedRowKeys.length}</strong> 个编排</span>
              {selectedRowKeys.length < selectableFilteredIds.length ? (
                <Button type="link" size="small" onClick={() => setSelectedRowKeys(selectableFilteredIds)}>选择筛选出的全部 {selectableFilteredIds.length} 条</Button>
              ) : selectableFilteredIds.length > 0 ? <span className="batch-action-bar__all">已选择全部筛选结果</span> : null}
            </div>
            <Space size="small" wrap>
              {onBatchConfig ? <Button type="primary" size="small" icon={<ControlOutlined />} onClick={() => onBatchConfig(selectedWorkflows)}>批量配置</Button> : null}
              <Button size="small" icon={<PlayCircleOutlined />} onClick={handleBatchActivate}>批量激活</Button>
              <Button size="small" icon={<PauseCircleOutlined />} onClick={handleBatchDeactivate}>批量停用</Button>
              <Button size="small" danger icon={<DeleteOutlined />} onClick={handleBatchDelete}>批量删除</Button>
              <Button size="small" icon={<CloseOutlined />} onClick={() => setSelectedRowKeys([])}>取消选择</Button>
            </Space>
          </div>
        ) : null}

        <Table
          dataSource={filteredWorkflows}
          columns={columns}
          rowKey="id"
          loading={loading}
          rowSelection={rowSelection}
          rowClassName={(record) => record.is_template ? 'workflow-template-row' : ''}
          scroll={{ x: 1550 }}
          pagination={{
            current: currentPage,
            pageSize,
            showSizeChanger: true,
            showQuickJumper: true,
            showTotal: (total) => `共 ${total} 条`,
            onChange: (page, size) => {
              setCurrentPage(page);
              setPageSize(size);
            },
          }}
          className="workflow-table"
        />
      </section>

      <Drawer title="按视频源筛选" placement="left" width={300} open={sourceDrawerOpen} onClose={() => setSourceDrawerOpen(false)} className="workflow-tree-drawer">
        {tree}
      </Drawer>
    </div>
  );
};

export default WorkflowTable;
