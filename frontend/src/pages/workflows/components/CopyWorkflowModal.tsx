import React, { useEffect, useMemo, useState } from 'react';
import { Alert, Checkbox, Input, Select, Tag } from 'antd';
import { CheckOutlined, CopyOutlined, FileTextOutlined, VideoCameraOutlined } from '@ant-design/icons';
import Button from '@/components/common/AppButton';
import AppEmptyState from '@/components/common/AppEmptyState';
import AppModal from '@/components/common/AppModal';
import type { VideoSource, Workflow } from '@/services/api';
import './CopyWorkflowModal.css';

type SourceStatusFilter = 'all' | 'running' | 'not_running';

export interface CopyWorkflowModalProps {
  visible: boolean;
  workflow: Workflow | null;
  workflows: Workflow[];
  videoSources: VideoSource[];
  onCopy: (sourceIds: number[]) => Promise<void>;
  onCancel: () => void;
}

const CopyWorkflowModal: React.FC<CopyWorkflowModalProps> = ({
  visible,
  workflow,
  workflows,
  videoSources,
  onCopy,
  onCancel,
}) => {
  const [selectedSourceIds, setSelectedSourceIds] = useState<number[]>([]);
  const [searchText, setSearchText] = useState('');
  const [statusFilter, setStatusFilter] = useState<SourceStatusFilter>('all');
  const [showExisting, setShowExisting] = useState(false);
  const [loading, setLoading] = useState(false);

  const existingBySourceId = useMemo(() => {
    const entries = workflows
      .filter((item) => item.source_template_id === workflow?.id && item.video_source_id != null)
      .map((item) => [Number(item.video_source_id), item] as const);
    return new Map(entries);
  }, [workflow?.id, workflows]);

  const filteredSources = useMemo(() => {
    const normalizedSearch = searchText.trim().toLocaleLowerCase('zh-CN');
    return videoSources.filter((source) => {
      const existing = existingBySourceId.has(Number(source.id));
      if (!showExisting && existing) return false;
      const matchesSearch = !normalizedSearch
        || source.name.toLocaleLowerCase('zh-CN').includes(normalizedSearch)
        || source.source_code.toLocaleLowerCase('zh-CN').includes(normalizedSearch);
      const isRunning = source.status === 'RUNNING';
      const matchesStatus = statusFilter === 'all'
        || (statusFilter === 'running' && isRunning)
        || (statusFilter === 'not_running' && !isRunning);
      return matchesSearch && matchesStatus;
    });
  }, [existingBySourceId, searchText, showExisting, statusFilter, videoSources]);

  const filteredAvailableIds = useMemo(
    () => filteredSources
      .filter((source) => !existingBySourceId.has(Number(source.id)))
      .map((source) => Number(source.id)),
    [existingBySourceId, filteredSources],
  );
  const selectedIdSet = useMemo(() => new Set(selectedSourceIds), [selectedSourceIds]);
  const selectedFilteredCount = filteredAvailableIds.filter((id) => selectedIdSet.has(id)).length;
  const allFilteredSelected = filteredAvailableIds.length > 0
    && selectedFilteredCount === filteredAvailableIds.length;
  const someFilteredSelected = selectedFilteredCount > 0 && !allFilteredSelected;

  useEffect(() => {
    if (!visible) return;
    setSelectedSourceIds([]);
    setSearchText('');
    setStatusFilter('all');
    setShowExisting(false);
  }, [visible, workflow?.id]);

  const handleSourceChange = (sourceId: number, checked: boolean) => {
    setSelectedSourceIds((current) => checked
      ? Array.from(new Set([...current, sourceId]))
      : current.filter((id) => id !== sourceId));
  };

  const handleSelectFiltered = (checked: boolean) => {
    const filteredIdSet = new Set(filteredAvailableIds);
    setSelectedSourceIds((current) => checked
      ? Array.from(new Set([...current, ...filteredAvailableIds]))
      : current.filter((id) => !filteredIdSet.has(id)));
  };

  const handleCopy = async () => {
    if (!selectedSourceIds.length) return;
    setLoading(true);
    try {
      await onCopy(selectedSourceIds);
      setSelectedSourceIds([]);
    } finally {
      setLoading(false);
    }
  };

  const filtersActive = Boolean(searchText.trim()) || statusFilter !== 'all' || showExisting;
  const allSourcesHaveWorkflow = videoSources.length > 0
    && videoSources.every((source) => existingBySourceId.has(Number(source.id)));
  const clearFilters = () => {
    setSearchText('');
    setStatusFilter('all');
    setShowExisting(false);
  };

  return (
    <AppModal
      title="将模板应用到视频源"
      description="每个选中的视频源都会创建一个可独立配置和调度的运行编排"
      open={visible}
      onCancel={onCancel}
      size="lg"
      className="copy-workflow-modal"
      closable={!loading}
      keyboard={!loading}
      footer={[
        <span key="summary" className="copy-workflow-footer__summary">
          已选择 <strong>{selectedSourceIds.length}</strong> 个视频源
        </span>,
        <Button key="cancel" onClick={onCancel} disabled={loading}>取消</Button>,
        <Button
          key="copy"
          type="primary"
          icon={<CopyOutlined />}
          onClick={handleCopy}
          disabled={!selectedSourceIds.length || loading}
          loading={loading}
        >
          创建 {selectedSourceIds.length} 个运行编排
        </Button>,
      ]}
    >
      <div className="copy-workflow-modal-content">
        <div className="copy-template-context">
          <span className="copy-template-context__icon"><FileTextOutlined /></span>
          <span>
            <small>当前模板</small>
            <strong>{workflow?.name || '未命名模板'}</strong>
            {workflow?.description ? <p>{workflow.description}</p> : null}
          </span>
        </div>

        <div className="copy-source-toolbar" role="search" aria-label="筛选目标视频源">
          <Input.Search
            allowClear
            aria-label="搜索目标视频源"
            placeholder="搜索视频源名称或编码"
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
          />
          <Select
            aria-label="按视频源状态筛选"
            value={statusFilter}
            onChange={setStatusFilter}
            options={[
              { value: 'all', label: '全部状态' },
              { value: 'running', label: '运行中' },
              { value: 'not_running', label: '非运行中' },
            ]}
          />
          <Checkbox checked={showExisting} onChange={(event) => setShowExisting(event.target.checked)}>
            显示已创建
          </Checkbox>
        </div>

        <div className="copy-source-selection-bar">
          <Checkbox
            checked={allFilteredSelected}
            indeterminate={someFilteredSelected}
            disabled={!filteredAvailableIds.length}
            onChange={(event) => handleSelectFiltered(event.target.checked)}
          >
            选择当前结果中的可用视频源（{filteredAvailableIds.length} 个）
          </Checkbox>
          <span>共显示 {filteredSources.length} 个视频源</span>
        </div>

        <div className="copy-video-sources-list">
          {!filteredSources.length ? (
            <AppEmptyState
              compact
              title={!videoSources.length
                ? '暂无可用的视频源'
                : allSourcesHaveWorkflow && !filtersActive
                  ? '所有视频源均已创建编排'
                  : '没有符合条件的视频源'}
              description={!videoSources.length
                ? '请先在视频源管理页面添加视频源。'
                : allSourcesHaveWorkflow && !filtersActive
                  ? '可以显示已创建项，查看对应的运行编排。'
                  : '调整搜索或状态条件后重试。'}
              action={filtersActive
                ? <Button onClick={clearFilters}>清除筛选</Button>
                : allSourcesHaveWorkflow
                  ? <Button onClick={() => setShowExisting(true)}>显示已创建项</Button>
                  : undefined}
            />
          ) : filteredSources.map((source) => {
            const sourceId = Number(source.id);
            const selected = selectedIdSet.has(sourceId);
            const existingWorkflow = existingBySourceId.get(sourceId);
            return (
              <label
                key={source.id}
                className={`copy-video-source ${selected ? 'is-selected' : ''} ${existingWorkflow ? 'is-disabled' : ''}`}
              >
                <Checkbox
                  checked={selected}
                  disabled={Boolean(existingWorkflow)}
                  onChange={(event) => handleSourceChange(sourceId, event.target.checked)}
                />
                <span className="copy-video-source__icon"><VideoCameraOutlined /></span>
                <span className="copy-video-source__copy">
                  <strong>{source.name}</strong>
                  <small>{source.source_code}</small>
                  {existingWorkflow ? <small>已有编排：{existingWorkflow.name}</small> : null}
                </span>
                <span className="copy-video-source__status">
                  {existingWorkflow ? (
                    <Tag>已创建</Tag>
                  ) : source.status === 'RUNNING' ? (
                    <Tag color="success" icon={<CheckOutlined />}>运行中</Tag>
                  ) : (
                    <Tag>非运行中</Tag>
                  )}
                </span>
              </label>
            );
          })}
        </div>

        {selectedSourceIds.length ? (
          <Alert
            message={`将创建 ${selectedSourceIds.length} 个运行编排，之后可分别调整参数和启停。`}
            type="success"
            showIcon
            className="copy-selection-feedback"
          />
        ) : null}
      </div>
    </AppModal>
  );
};

export default CopyWorkflowModal;
