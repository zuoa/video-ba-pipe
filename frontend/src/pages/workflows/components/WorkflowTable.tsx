import { getDateLocale } from '@/i18n/tr';
import { tr, trf } from '@/i18n/tr';
import React, { useDeferredValue, useEffect, useMemo, useState } from 'react';
import { Badge, Dropdown, Input, Select, Space, Table, Tag } from 'antd';
import type { MenuProps } from 'antd';
import { useNavigate } from 'umi';
import Button from '@/components/common/AppButton';
import AppEmptyState from '@/components/common/AppEmptyState';
import {
  ApartmentOutlined,
  CloseOutlined,
  ControlOutlined,
  CopyOutlined,
  DeleteOutlined,
  EditOutlined,
  EllipsisOutlined,
  FileTextOutlined,
  ExportOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons';
import type { VideoSource, Workflow } from '@/services/api';
import {
  defaultRuntimeFilters,
  filterRuntimeWorkflows,
  getWorkflowSourceId,
  hasActiveRuntimeFilters,
  workflowMatchesSearch,
  type RuntimeWorkflowFilters,
} from '../utils/workflowList';
import './WorkflowTable.css';

const { Search } = Input;

export interface WorkflowTableProps {
  workflows: Workflow[];
  loading: boolean;
  videoSources: VideoSource[];
  onEdit: (workflow: Workflow) => void;
  onDelete: (id: number) => void;
  onActivate: (id: number) => void;
  onDeactivate: (id: number) => void;
  onCopy?: (workflow: Workflow) => void;
  onExport?: (workflow: Workflow) => void;
  onBatchActivate?: (ids: number[]) => void;
  onBatchDeactivate?: (ids: number[]) => void;
  onBatchDelete?: (ids: number[]) => void;
  onBatchConfig?: (workflows: Workflow[]) => void;
}

const formatUpdatedAt = (date?: string | null) => {
  if (!date) return '-';
  return new Date(date).toLocaleString(getDateLocale(), {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
};

const WorkflowTable: React.FC<WorkflowTableProps> = ({
  workflows,
  loading,
  videoSources,
  onEdit,
  onDelete,
  onActivate,
  onDeactivate,
  onCopy,
  onExport,
  onBatchActivate,
  onBatchDeactivate,
  onBatchDelete,
  onBatchConfig,
}) => {
  const navigate = useNavigate();
  const [templateSearch, setTemplateSearch] = useState('');
  const [runtimeFilters, setRuntimeFilters] = useState<RuntimeWorkflowFilters>(defaultRuntimeFilters);
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [templatePage, setTemplatePage] = useState(1);
  const [runtimePage, setRuntimePage] = useState(1);
  const [runtimePageSize, setRuntimePageSize] = useState(10);
  const deferredTemplateSearch = useDeferredValue(templateSearch);
  const deferredRuntimeSearch = useDeferredValue(runtimeFilters.search);

  const effectiveRuntimeFilters = useMemo(
    () => ({
      search: deferredRuntimeSearch,
      source: runtimeFilters.source,
      origin: runtimeFilters.origin,
      status: runtimeFilters.status,
    }),
    [
      deferredRuntimeSearch,
      runtimeFilters.origin,
      runtimeFilters.source,
      runtimeFilters.status,
    ],
  );

  const templates = useMemo(
    () => workflows.filter((workflow) => workflow.is_template),
    [workflows],
  );
  const runtimeWorkflows = useMemo(
    () => workflows.filter((workflow) => !workflow.is_template),
    [workflows],
  );
  const templateRows = useMemo(
    () => templates.filter((workflow) => workflowMatchesSearch(workflow, deferredTemplateSearch)),
    [deferredTemplateSearch, templates],
  );
  const runtimeRows = useMemo(
    () => filterRuntimeWorkflows(runtimeWorkflows, effectiveRuntimeFilters),
    [effectiveRuntimeFilters, runtimeWorkflows],
  );

  const sourcesById = useMemo(
    () => new Map(videoSources.map((source) => [Number(source.id), source])),
    [videoSources],
  );
  const templatesById = useMemo(
    () => new Map(templates.map((template) => [template.id, template])),
    [templates],
  );
  const templateUsageCounts = useMemo(() => {
    const counts = new Map<number, number>();
    runtimeWorkflows.forEach((workflow) => {
      if (workflow.source_template_id != null) {
        counts.set(workflow.source_template_id, (counts.get(workflow.source_template_id) || 0) + 1);
      }
    });
    return counts;
  }, [runtimeWorkflows]);

  const selectedWorkflows = useMemo(() => {
    const selectedIds = new Set(selectedRowKeys.map(Number));
    return runtimeWorkflows.filter((workflow) => selectedIds.has(workflow.id));
  }, [runtimeWorkflows, selectedRowKeys]);
  const selectableFilteredIds = useMemo(
    () => runtimeRows.map((workflow) => workflow.id),
    [runtimeRows],
  );
  const filtersActive = hasActiveRuntimeFilters(runtimeFilters);

  useEffect(() => {
    setTemplatePage(1);
  }, [deferredTemplateSearch]);

  useEffect(() => {
    setSelectedRowKeys([]);
    setRuntimePage(1);
  }, [deferredRuntimeSearch, runtimeFilters.origin, runtimeFilters.source, runtimeFilters.status]);

  const updateRuntimeFilter = <K extends keyof RuntimeWorkflowFilters>(
    key: K,
    value: RuntimeWorkflowFilters[K],
  ) => {
    setRuntimeFilters((current) => ({ ...current, [key]: value }));
  };

  const clearRuntimeFilters = () => setRuntimeFilters(defaultRuntimeFilters);

  const handleBatchActivate = () => {
    if (!selectedRowKeys.length) return;
    if (onBatchActivate) onBatchActivate(selectedRowKeys.map(Number));
    else selectedRowKeys.forEach((id) => onActivate(Number(id)));
    setSelectedRowKeys([]);
  };

  const handleBatchDeactivate = () => {
    if (!selectedRowKeys.length) return;
    if (onBatchDeactivate) onBatchDeactivate(selectedRowKeys.map(Number));
    else selectedRowKeys.forEach((id) => onDeactivate(Number(id)));
    setSelectedRowKeys([]);
  };

  const handleBatchDelete = () => {
    if (!selectedRowKeys.length) return;
    if (onBatchDelete) onBatchDelete(selectedRowKeys.map(Number));
    else selectedRowKeys.forEach((id) => onDelete(Number(id)));
    setSelectedRowKeys([]);
  };

  const getTemplateMenu = (record: Workflow): MenuProps => ({
    items: [
      { key: 'structure', icon: <ApartmentOutlined />, label: tr("编辑模板结构") },
      { key: 'details', icon: <EditOutlined />, label: tr("编辑基本信息") },
      ...(onExport
        ? [{ key: 'export', icon: <ExportOutlined />, label: tr("导出迁移包") }]
        : []),
      { type: 'divider' },
      { key: 'delete', icon: <DeleteOutlined />, label: tr("删除模板"), danger: true },
    ],
    onClick: ({ key }) => {
      if (key === 'structure') navigate(`/workflows/editor/${record.id}`);
      if (key === 'details') onEdit(record);
      if (key === 'export') onExport?.(record);
      if (key === 'delete') onDelete(record.id);
    },
  });

  const getRuntimeMenu = (record: Workflow): MenuProps => ({
    items: [
      { key: 'details', icon: <EditOutlined />, label: tr("编辑基本信息") },
      record.is_active
        ? { key: 'deactivate', icon: <PauseCircleOutlined />, label: tr("停用编排") }
        : { key: 'activate', icon: <PlayCircleOutlined />, label: tr("激活编排") },
      { type: 'divider' },
      { key: 'delete', icon: <DeleteOutlined />, label: tr("删除编排"), danger: true },
    ],
    onClick: ({ key }) => {
      if (key === 'details') onEdit(record);
      if (key === 'activate') onActivate(record.id);
      if (key === 'deactivate') onDeactivate(record.id);
      if (key === 'delete') onDelete(record.id);
    },
  });

  const templateColumns = [
    {
      title: tr("模板"),
      dataIndex: 'name',
      key: 'name',
      render: (name: string, record: Workflow) => (
        <div className="workflow-name-cell">
          <span className="workflow-name-cell__icon workflow-name-cell__icon--template"><FileTextOutlined /></span>
          <span className="workflow-name-cell__copy">
            <strong>{name || tr("未命名模板")}</strong>
            <span>{record.description || tr("暂无用途说明")}</span>
          </span>
        </div>
      ),
    },
    {
      title: tr("已生成编排"),
      key: 'usage_count',
      width: 150,
      render: (_: unknown, record: Workflow) => (
        <span className="template-usage"><strong>{templateUsageCounts.get(record.id) || 0}</strong> {tr("个")}</span>
      ),
    },
    {
      title: tr("更新时间"),
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 180,
      render: formatUpdatedAt,
    },
    {
      title: tr("操作"),
      key: 'action',
      width: 200,
      fixed: 'right' as const,
      render: (_: unknown, record: Workflow) => (
        <Space size={8}>
          <Button
            type="primary"
            size="small"
            icon={<CopyOutlined />}
            onClick={() => onCopy?.(record)}
            disabled={!onCopy}
          >
            {tr("应用到视频源")}
          </Button>
          <Dropdown menu={getTemplateMenu(record)} trigger={['click']} placement="bottomRight">
            <Button iconOnly size="small" icon={<EllipsisOutlined />} aria-label={trf("更多模板操作：__VAR0__", [record.name])} />
          </Dropdown>
        </Space>
      ),
    },
  ];

  const runtimeColumns = [
    {
      title: tr("运行编排"),
      dataIndex: 'name',
      key: 'name',
      width: 300,
      render: (name: string, record: Workflow) => (
        <div className="workflow-name-cell">
          <span className="workflow-name-cell__icon"><ApartmentOutlined /></span>
          <span className="workflow-name-cell__copy">
            <strong>{name || tr("未命名编排")}</strong>
            <span>{record.description || tr("暂无用途说明")}</span>
          </span>
        </div>
      ),
    },
    {
      title: tr("视频源"),
      key: 'video_source',
      width: 210,
      render: (_: unknown, record: Workflow) => {
        const sourceId = getWorkflowSourceId(record);
        const source = sourceId == null ? undefined : sourcesById.get(sourceId);
        if (sourceId == null) return <Tag>{tr("未绑定视频源")}</Tag>;
        if (!source) return <Tag color="warning">{tr("视频源不可用 · #")}{sourceId}</Tag>;
        return (
          <div className="source-cell">
            <VideoCameraOutlined />
            <span><strong>{source.name}</strong><small>{source.source_code}</small></span>
          </div>
        );
      },
    },
    {
      title: tr("创建来源"),
      key: 'source_template',
      width: 180,
      render: (_: unknown, record: Workflow) => record.source_template_id ? (
        <Tag color="geekblue">
          {record.source_template_name || templatesById.get(record.source_template_id)?.name || trf("模板 #__VAR0__", [record.source_template_id])}
        </Tag>
      ) : <span className="muted-cell">{tr("自主创建")}</span>,
    },
    {
      title: tr("状态"),
      dataIndex: 'is_active',
      key: 'is_active',
      width: 120,
      render: (isActive: boolean) => (
        <Badge
          status={isActive ? 'processing' : 'default'}
          text={<span className={isActive ? 'status-active' : 'status-inactive'}>{isActive ? tr("运行中") : tr("已停用")}</span>}
        />
      ),
    },
    {
      title: tr("更新时间"),
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 180,
      render: formatUpdatedAt,
    },
    {
      title: tr("操作"),
      key: 'action',
      width: 150,
      fixed: 'right' as const,
      render: (_: unknown, record: Workflow) => (
        <Space size={8}>
          <Button size="small" icon={<ApartmentOutlined />} onClick={() => navigate(`/workflows/editor/${record.id}`)}>
            {tr("编排")}
          </Button>
          <Dropdown menu={getRuntimeMenu(record)} trigger={['click']} placement="bottomRight">
            <Button iconOnly size="small" icon={<EllipsisOutlined />} aria-label={trf("更多编排操作：__VAR0__", [record.name])} />
          </Dropdown>
        </Space>
      ),
    },
  ];

  const runtimeRowSelection = {
    selectedRowKeys,
    preserveSelectedRowKeys: false,
    onChange: setSelectedRowKeys,
  };

  const sourceLabel = runtimeFilters.source === 'unbound'
    ? tr("视频源：未绑定")
    : runtimeFilters.source.startsWith('source:')
      ? trf("视频源：__VAR0__", [sourcesById.get(Number(runtimeFilters.source.slice(7)))?.name || tr("已删除视频源")])
      : '';
  const originLabel = runtimeFilters.origin === 'direct'
    ? tr("来源：自主创建")
    : runtimeFilters.origin.startsWith('template:')
      ? trf("来源：__VAR0__", [templatesById.get(Number(runtimeFilters.origin.slice(9)))?.name || tr("已删除模板")])
      : '';

  return (
    <div className="workflow-sections">
      <section className="workflow-section workflow-section--templates" aria-labelledby="workflow-templates-title">
        <header className="workflow-section__header">
          <div className="workflow-section__identity">
            <span className="workflow-section__icon workflow-section__icon--template"><FileTextOutlined /></span>
            <div>
              <span className="workflow-section__eyebrow">{tr("可复用配置")}</span>
              <h2 id="workflow-templates-title">{tr("编排模板")}</h2>
              <p>{tr("模板不绑定视频源且不会调度，可批量应用到不同视频源。")}</p>
            </div>
          </div>
          <span className="workflow-section__count"><strong>{templates.length}</strong> {tr("个模板")}</span>
        </header>

        <div className="workflow-section__toolbar workflow-section__toolbar--simple">
          <Search
            aria-label={tr("搜索编排模板")}
            allowClear
            placeholder={tr("搜索模板名称或描述")}
            value={templateSearch}
            onChange={(event) => setTemplateSearch(event.target.value)}
          />
          <span className="workflow-results-summary">{tr("显示")} {templateRows.length} / {templates.length} {tr("个模板")}</span>
        </div>

        <Table
          dataSource={templateRows}
          columns={templateColumns}
          rowKey="id"
          loading={loading}
          scroll={{ x: 860 }}
          pagination={templateRows.length > 5 ? {
            current: templatePage,
            pageSize: 5,
            showTotal: (total) => trf("共 __VAR0__ 个模板", [total]),
            onChange: setTemplatePage,
          } : false}
          locale={{
            emptyText: (
              <AppEmptyState
                compact
                title={templateSearch ? tr("没有匹配的编排模板") : tr("暂无编排模板")}
                description={templateSearch ? tr("尝试其他关键词，或清除当前搜索。") : tr("新建算法编排时选择“编排模板”即可创建。")}
                action={templateSearch ? <Button onClick={() => setTemplateSearch('')}>{tr("清除搜索")}</Button> : undefined}
              />
            ),
          }}
          className="workflow-table workflow-template-table"
        />
      </section>

      <section className="workflow-section workflow-section--runtime" aria-labelledby="workflow-runtime-title">
        <header className="workflow-section__header">
          <div className="workflow-section__identity">
            <span className="workflow-section__icon"><ApartmentOutlined /></span>
            <div>
              <span className="workflow-section__eyebrow">{tr("视频分析任务")}</span>
              <h2 id="workflow-runtime-title">{tr("运行编排")}</h2>
              <p>{tr("绑定视频源后可激活调度，支持筛选、批量配置与启停。")}</p>
            </div>
          </div>
          <span className="workflow-section__count"><strong>{runtimeWorkflows.length}</strong> {tr("个编排")}</span>
        </header>

        <div className="workflow-section__toolbar workflow-runtime-filters" role="search" aria-label={tr("筛选运行编排")}>
          <Search
            aria-label={tr("搜索运行编排")}
            allowClear
            placeholder={tr("搜索名称或描述")}
            value={runtimeFilters.search}
            onChange={(event) => updateRuntimeFilter('search', event.target.value)}
          />
          <Select
            aria-label={tr("按视频源筛选")}
            showSearch
            optionFilterProp="label"
            value={runtimeFilters.source}
            onChange={(value) => updateRuntimeFilter('source', value)}
            options={[
              { value: 'all', label: tr("全部视频源") },
              { value: 'unbound', label: tr("未绑定视频源") },
              ...videoSources.map((source) => ({ value: `source:${source.id}`, label: `${source.name} · ${source.source_code}` })),
            ]}
          />
          <Select
            aria-label={tr("按来源模板筛选")}
            showSearch
            optionFilterProp="label"
            value={runtimeFilters.origin}
            onChange={(value) => updateRuntimeFilter('origin', value)}
            options={[
              { value: 'all', label: tr("全部创建来源") },
              { value: 'direct', label: tr("自主创建") },
              ...templates.map((template) => ({ value: `template:${template.id}`, label: template.name })),
            ]}
          />
          <Select
            aria-label={tr("按运行状态筛选")}
            value={runtimeFilters.status}
            onChange={(value) => updateRuntimeFilter('status', value)}
            options={[
              { value: 'all', label: tr("全部运行状态") },
              { value: 'active', label: tr("运行中") },
              { value: 'inactive', label: tr("已停用") },
            ]}
          />
          <span className="workflow-results-summary">{tr("显示")} {runtimeRows.length} / {runtimeWorkflows.length} {tr("个编排")}</span>
        </div>

        {filtersActive ? (
          <div className="workflow-active-filters" aria-label={tr("当前筛选条件")}>
            <span>{tr("当前筛选")}</span>
            {runtimeFilters.search.trim() ? (
              <Tag closable onClose={() => updateRuntimeFilter('search', '')}>{tr("关键词：")}{runtimeFilters.search.trim()}</Tag>
            ) : null}
            {sourceLabel ? <Tag closable onClose={() => updateRuntimeFilter('source', 'all')}>{sourceLabel}</Tag> : null}
            {originLabel ? <Tag closable onClose={() => updateRuntimeFilter('origin', 'all')}>{originLabel}</Tag> : null}
            {runtimeFilters.status !== 'all' ? (
              <Tag closable onClose={() => updateRuntimeFilter('status', 'all')}>
                {tr("状态：")}{runtimeFilters.status === 'active' ? tr("运行中") : tr("已停用")}
              </Tag>
            ) : null}
            <Button type="link" size="small" onClick={clearRuntimeFilters}>{tr("清除全部")}</Button>
          </div>
        ) : null}

        {selectedRowKeys.length > 0 ? (
          <div className="batch-action-bar" role="region" aria-label={tr("批量操作")}>
            <div className="batch-action-bar__selection">
              <span>{tr("已选择")} <strong>{selectedRowKeys.length}</strong> {tr("个编排")}</span>
              {selectedRowKeys.length < selectableFilteredIds.length ? (
                <Button type="link" size="small" onClick={() => setSelectedRowKeys(selectableFilteredIds)}>
                  {tr("选择筛选出的全部")} {selectableFilteredIds.length} {tr("条")}
                </Button>
              ) : <span className="batch-action-bar__all">{tr("已选择全部筛选结果")}</span>}
            </div>
            <Space size="small" wrap>
              {onBatchConfig ? <Button type="primary" size="small" icon={<ControlOutlined />} onClick={() => onBatchConfig(selectedWorkflows)}>{tr("批量配置")}</Button> : null}
              <Button size="small" icon={<PlayCircleOutlined />} onClick={handleBatchActivate}>{tr("批量激活")}</Button>
              <Button size="small" icon={<PauseCircleOutlined />} onClick={handleBatchDeactivate}>{tr("批量停用")}</Button>
              <Button size="small" danger icon={<DeleteOutlined />} onClick={handleBatchDelete}>{tr("批量删除")}</Button>
              <Button size="small" icon={<CloseOutlined />} onClick={() => setSelectedRowKeys([])}>{tr("取消选择")}</Button>
            </Space>
          </div>
        ) : null}

        <Table
          dataSource={runtimeRows}
          columns={runtimeColumns}
          rowKey="id"
          loading={loading}
          rowSelection={runtimeRowSelection}
          scroll={{ x: 1180 }}
          pagination={{
            current: runtimePage,
            pageSize: runtimePageSize,
            showSizeChanger: true,
            showQuickJumper: true,
            showTotal: (total) => trf("共 __VAR0__ 个编排", [total]),
            onChange: (page, size) => {
              setRuntimePage(page);
              setRuntimePageSize(size);
            },
          }}
          locale={{
            emptyText: (
              <AppEmptyState
                compact
                title={filtersActive ? tr("没有符合条件的运行编排") : tr("暂无运行编排")}
                description={filtersActive ? tr("调整筛选条件，或清除全部筛选后重试。") : tr("新建普通编排，或从模板应用到视频源。")}
                action={filtersActive ? <Button onClick={clearRuntimeFilters}>{tr("清除全部筛选")}</Button> : undefined}
              />
            ),
          }}
          className="workflow-table workflow-runtime-table"
        />
      </section>
    </div>
  );
};

export default WorkflowTable;
