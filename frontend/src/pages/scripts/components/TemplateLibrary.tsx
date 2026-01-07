import React, { useState, useEffect } from 'react';
import { Modal, message, Empty, Spin, Space, Button } from 'antd';
import {
  FileTextOutlined,
  EyeOutlined,
  CopyOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import './TemplateLibrary.css';

export interface TemplateLibraryProps {
  visible: boolean;
  onClose: () => void;
  onUseTemplate: (content: string, path: string) => void;
}

const TemplateLibrary: React.FC<TemplateLibraryProps> = ({
  visible,
  onClose,
  onUseTemplate,
}) => {
  const [templates, setTemplates] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [viewingTemplate, setViewingTemplate] = useState<any>(null);
  const [viewModalVisible, setViewModalVisible] = useState(false);

  useEffect(() => {
    if (visible) {
      loadTemplates();
    }
  }, [visible]);

  const loadTemplates = async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/scripts/templates');
      const data = await response.json();

      if (data.success) {
        setTemplates(data.templates || []);
      } else {
        message.error('加载模板失败: ' + data.error);
      }
    } catch (error) {
      message.error('加载失败，请检查网络连接');
    } finally {
      setLoading(false);
    }
  };

  const handleViewTemplate = (template: any) => {
    setViewingTemplate(template);
    setViewModalVisible(true);
  };

  const handleUseTemplate = (template: any) => {
    onUseTemplate(template.content, template.path);
  };

  const getCategoryColor = (path: string) => {
    const colors: Record<string, string> = {
      detectors: 'blue',
      filters: 'green',
      hooks: 'orange',
      postprocessors: 'purple',
    };

    const category = path.split('/')[0];
    return colors[category] || 'default';
  };

  return (
    <>
      <Modal
        title={
          <Space>
            <div className="modal-icon template-icon">
              <FileTextOutlined />
            </div>
            <span>脚本模板库</span>
          </Space>
        }
        open={visible}
        onCancel={onClose}
        footer={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={loadTemplates} loading={loading}>
              刷新
            </Button>
            <Button type="primary" onClick={onClose}>
              关闭
            </Button>
          </Space>
        }
        width={900}
        className="template-library-modal"
        centered
      >
        <Spin spinning={loading}>
          <div className="templates-grid">
            {templates.length === 0 ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={
                  <div className="empty-templates">
                    <FileTextOutlined style={{ fontSize: 48, color: '#d9d9d9' }} />
                    <p style={{ marginTop: 16, color: '#8c8c8c' }}>暂无可用模板</p>
                  </div>
                }
              />
            ) : (
              templates.map((template, index) => (
                <div key={index} className="template-card">
                  <div className="template-header">
                    <div className="template-icon">
                      <FileTextOutlined />
                    </div>
                    <div className="template-info">
                      <div className="template-name">{template.name}</div>
                      <div className="template-path">
                        <code>{template.path}</code>
                      </div>
                    </div>
                  </div>
                  <div className="template-actions">
                    <Button
                      size="small"
                      icon={<EyeOutlined />}
                      onClick={() => handleViewTemplate(template)}
                      className="action-btn action-btn-view"
                    >
                      查看
                    </Button>
                    <Button
                      size="small"
                      type="primary"
                      icon={<CopyOutlined />}
                      onClick={() => handleUseTemplate(template)}
                      className="action-btn action-btn-use"
                    >
                      使用
                    </Button>
                  </div>
                </div>
              ))
            )}
          </div>
        </Spin>
      </Modal>

      {/* 查看模板模态框 */}
      <Modal
        title={
          <Space>
            <div className="modal-icon">
              <FileTextOutlined />
            </div>
            <div>
              <div>{viewingTemplate?.name}</div>
              <div className="modal-subtitle">{viewingTemplate?.path}</div>
            </div>
          </Space>
        }
        open={viewModalVisible}
        onCancel={() => setViewModalVisible(false)}
        footer={
          <Space>
            <Button onClick={() => setViewModalVisible(false)}>关闭</Button>
            <Button
              type="primary"
              icon={<CopyOutlined />}
              onClick={() => {
                if (viewingTemplate) {
                  handleUseTemplate(viewingTemplate);
                  setViewModalVisible(false);
                }
              }}
            >
              使用此模板
            </Button>
          </Space>
        }
        width={900}
        className="view-template-modal"
        centered
      >
        <div className="template-content">
          <pre className="code-preview">
            <code>{viewingTemplate?.content}</code>
          </pre>
        </div>
      </Modal>
    </>
  );
};

export default TemplateLibrary;
