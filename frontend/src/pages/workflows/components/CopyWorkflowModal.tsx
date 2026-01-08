import React, { useState } from 'react';
import { Modal, Checkbox, Button, Space, Typography, Alert, Divider } from 'antd';
import { CopyOutlined, CheckOutlined } from '@ant-design/icons';
import './CopyWorkflowModal.css';

const { Text, Paragraph } = Typography;

export interface CopyWorkflowModalProps {
  visible: boolean;
  workflow: any;
  videoSources: any[];
  onCopy: (sourceIds: number[]) => Promise<void>;
  onCancel: () => void;
}

const CopyWorkflowModal: React.FC<CopyWorkflowModalProps> = ({
  visible,
  workflow,
  videoSources,
  onCopy,
  onCancel,
}) => {
  const [selectedSourceIds, setSelectedSourceIds] = useState<number[]>([]);
  const [loading, setLoading] = useState(false);

  const handleSourceChange = (sourceId: number, checked: boolean) => {
    if (checked) {
      setSelectedSourceIds([...selectedSourceIds, sourceId]);
    } else {
      setSelectedSourceIds(selectedSourceIds.filter((id) => id !== sourceId));
    }
  };

  const handleSelectAll = (checked: boolean) => {
    if (checked) {
      setSelectedSourceIds(videoSources.map((s) => s.id));
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

  const isAllSelected = videoSources.length > 0 && selectedSourceIds.length === videoSources.length;
  const isIndeterminate = selectedSourceIds.length > 0 && selectedSourceIds.length < videoSources.length;

  return (
    <Modal
      title={
        <Space>
          <CopyOutlined />
          <span>复制编排到其他视频源</span>
        </Space>
      }
      open={visible}
      onCancel={onCancel}
      width={600}
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
        {videoSources.length > 0 && (
          <div className="select-all-section">
            <Checkbox
              checked={isAllSelected}
              indeterminate={isIndeterminate}
              onChange={(e) => handleSelectAll(e.target.checked)}
            >
              <Text strong>全选 ({videoSources.length} 个视频源)</Text>
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
                return (
                  <div
                    key={source.id}
                    className={`video-source-item ${isSelected ? 'selected' : ''}`}
                  >
                    <Checkbox
                      checked={isSelected}
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
                        {source.source_code}
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
    </Modal>
  );
};

export default CopyWorkflowModal;
