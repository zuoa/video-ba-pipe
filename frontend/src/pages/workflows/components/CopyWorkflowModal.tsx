import React, { useEffect, useMemo, useState } from 'react';
import { Checkbox, Space, Typography, Alert, Divider } from 'antd';
import Button from '@/components/common/AppButton';
import { CopyOutlined, CheckOutlined } from '@ant-design/icons';
import './CopyWorkflowModal.css';
import AppModal from '@/components/common/AppModal';

const { Text, Paragraph } = Typography;

export interface CopyWorkflowModalProps {
  visible: boolean;
  workflow: any;
  workflows: any[];
  videoSources: any[];
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
  const [loading, setLoading] = useState(false);

  const existingBySourceId = useMemo(() => {
    const entries = workflows
      .filter((item) => item.source_template_id === workflow?.id && item.video_source_id != null)
      .map((item) => [Number(item.video_source_id), item] as const);
    return new Map(entries);
  }, [workflow?.id, workflows]);

  const availableSources = useMemo(
    () => videoSources.filter((source) => !existingBySourceId.has(Number(source.id))),
    [existingBySourceId, videoSources],
  );

  useEffect(() => {
    if (!visible) {
      setSelectedSourceIds([]);
    }
  }, [visible, workflow?.id]);

  const handleSourceChange = (sourceId: number, checked: boolean) => {
    if (checked) {
      setSelectedSourceIds((current) => [...current, sourceId]);
    } else {
      setSelectedSourceIds((current) => current.filter((id) => id !== sourceId));
    }
  };

  const handleSelectAll = (checked: boolean) => {
    if (checked) {
      setSelectedSourceIds(availableSources.map((s) => s.id));
    } else {
      setSelectedSourceIds([]);
    }
  };

  const handleCopy = async () => {
    if (selectedSourceIds.length === 0) {
      return;
    }

    setLoading(true);
    try {
      await onCopy(selectedSourceIds);
      setSelectedSourceIds([]);
    } finally {
      setLoading(false);
    }
  };

  const isAllSelected = availableSources.length > 0 && selectedSourceIds.length === availableSources.length;
  const isIndeterminate = selectedSourceIds.length > 0 && selectedSourceIds.length < availableSources.length;

  return (
    <AppModal
      title="复制编排到其他视频源"
      description="选择目标视频源并创建独立的编排副本"
      open={visible}
      onCancel={onCancel}
      size="md"
      closable={!loading}
      keyboard={!loading}
      footer={[
        <Button key="cancel" onClick={onCancel} disabled={loading}>
          取消
        </Button>,
        <Button
          key="copy"
          type="primary"
          icon={<CopyOutlined />}
          onClick={handleCopy}
          disabled={selectedSourceIds.length === 0 || loading}
          loading={loading}
        >
          复制到 {selectedSourceIds.length} 个视频源
        </Button>,
      ]}
    >
      <div className="copy-workflow-modal-content">
        {/* 当前工作流信息 */}
        <Alert
          message={
            <Space direction="vertical" size={0} style={{ width: '100%' }}>
              <Text strong>当前编排：</Text>
              <Text>{workflow?.name || '未命名'}</Text>
              {workflow?.description && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {workflow.description}
                </Text>
              )}
            </Space>
          }
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
        />

        <Paragraph type="secondary" style={{ marginBottom: 12 }}>
          选择要应用此编排的视频源，系统将为每个选中的视频源创建一个新的编排副本：
        </Paragraph>

        {/* 全选选项 */}
        {availableSources.length > 0 && (
          <div className="select-all-section">
            <Checkbox
              checked={isAllSelected}
              indeterminate={isIndeterminate}
              onChange={(e) => handleSelectAll(e.target.checked)}
            >
              <Text strong>全选可用视频源 ({availableSources.length} 个)</Text>
            </Checkbox>
            <Divider style={{ margin: '12px 0' }} />
          </div>
        )}

        {/* 视频源列表 */}
        <div className="video-sources-list">
          {videoSources.length === 0 ? (
            <Alert message="暂无可用的视频源" type="warning" showIcon />
          ) : (
            <Space direction="vertical" style={{ width: '100%' }} size={8}>
              {videoSources.map((source) => {
                const isSelected = selectedSourceIds.includes(source.id);
                const existingWorkflow = existingBySourceId.get(Number(source.id));
                return (
                  <div
                    key={source.id}
                    className={`video-source-item ${isSelected ? 'selected' : ''} ${existingWorkflow ? 'disabled' : ''}`}
                  >
                    <Checkbox
                      checked={isSelected}
                      disabled={Boolean(existingWorkflow)}
                      onChange={(e) => handleSourceChange(source.id, e.target.checked)}
                    >
                      <Space>
                        <Text strong>{source.name}</Text>
                        {source.status === 'RUNNING' && (
                          <CheckOutlined style={{ color: '#52c41a', fontSize: 12 }} />
                        )}
                      </Space>
                    </Checkbox>
                    <div className="source-details">
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {existingWorkflow
                          ? `已创建：${existingWorkflow.name}`
                          : source.source_code}
                      </Text>
                    </div>
                  </div>
                );
              })}
            </Space>
          )}
        </div>

        {/* 已选数量提示 */}
        {selectedSourceIds.length > 0 && (
          <Alert
            message={
              <Text>
                已选择 <Text strong>{selectedSourceIds.length}</Text> 个视频源，
                将创建 <Text strong>{selectedSourceIds.length}</Text> 个编排副本
              </Text>
            }
            type="success"
            showIcon
            style={{ marginTop: 16 }}
          />
        )}
      </div>
    </AppModal>
  );
};

export default CopyWorkflowModal;
