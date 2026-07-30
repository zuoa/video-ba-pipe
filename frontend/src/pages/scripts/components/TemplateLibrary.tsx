import React, { useState, useEffect } from 'react';
import { message, Spin, Space, Tooltip } from 'antd';
import Button from '@/components/common/AppButton';
import AppEmptyState from '@/components/common/AppEmptyState';
import {
  FileTextOutlined,
  EyeOutlined,
  CopyOutlined,
  ReloadOutlined,
  DownloadOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import { getScriptTemplates } from '@/services/api';
import './TemplateLibrary.css';
import AppModal from '@/components/common/AppModal';

export interface TemplateLibraryProps {
  visible: boolean;
  onClose: () => void;
  onUseTemplate: (content: string, path: string, isClone: boolean) => void;
  onDownloadTemplate: (template: any) => void;
}

const TemplateLibrary: React.FC<TemplateLibraryProps> = ({
  visible,
  onClose,
  onUseTemplate,
  onDownloadTemplate,
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
      const data = await getScriptTemplates();

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
    onUseTemplate(template.content, template.path, false);
  };

  const handleCloneTemplate = (template: any) => {
    onUseTemplate(template.content, template.path, true);
  };

  const handleDownloadTemplate = (template: any) => {
    onDownloadTemplate(template);
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
      <AppModal
        title="脚本模板库"
        description="浏览、下载或克隆内置算法脚本模板"
        size="xl"
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
        className="template-library-modal"
        centered
      >
        <Spin spinning={loading}>
          <div className="templates-grid">
            {templates.length === 0 ? (
              <AppEmptyState
                compact
                image={<FileTextOutlined className="empty-templates__icon" />}
                title="暂无可用模板"
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
                    <Space size="small">
                      <Tooltip title="查看模板代码">
                        <Button
                          size="small"
                          icon={<EyeOutlined />}
                          onClick={() => handleViewTemplate(template)}
                          className="action-btn action-btn-view"
                        >
                          查看
                        </Button>
                      </Tooltip>
                      <Tooltip title="下载到本地">
                        <Button
                          size="small"
                          icon={<DownloadOutlined />}
                          onClick={() => handleDownloadTemplate(template)}
                          className="action-btn action-btn-download"
                        >
                          下载
                        </Button>
                      </Tooltip>
                      <Tooltip title="在线编辑此模板">
                        <Button
                          size="small"
                          icon={<CopyOutlined />}
                          onClick={() => handleUseTemplate(template)}
                          className="action-btn action-btn-use"
                        >
                          使用
                        </Button>
                      </Tooltip>
                      <Tooltip title="克隆为新脚本">
                        <Button
                          size="small"
                          type="primary"
                          icon={<PlusOutlined />}
                          onClick={() => handleCloneTemplate(template)}
                          className="action-btn action-btn-clone"
                        >
                          克隆
                        </Button>
                      </Tooltip>
                    </Space>
                  </div>
                </div>
              ))
            )}
          </div>
        </Spin>
      </AppModal>

      {/* 查看模板模态框 */}
      <AppModal
        title={viewingTemplate?.name || '模板详情'}
        description={viewingTemplate?.path}
        kind="detail"
        size="xl"
        open={viewModalVisible}
        onCancel={() => setViewModalVisible(false)}
        footer={
          <Space>
            <Button
              icon={<DownloadOutlined />}
              onClick={() => {
                if (viewingTemplate) {
                  handleDownloadTemplate(viewingTemplate);
                }
              }}
            >
              下载模板
            </Button>
            <Button onClick={() => setViewModalVisible(false)}>关闭</Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => {
                if (viewingTemplate) {
                  handleCloneTemplate(viewingTemplate);
                  setViewModalVisible(false);
                }
              }}
            >
              克隆为新脚本
            </Button>
          </Space>
        }
        className="view-template-modal"
        centered
      >
        <div className="template-content">
          <pre className="code-preview">
            <code>{viewingTemplate?.content}</code>
          </pre>
        </div>
      </AppModal>
    </>
  );
};

export default TemplateLibrary;
