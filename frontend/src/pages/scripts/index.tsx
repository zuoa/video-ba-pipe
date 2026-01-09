import { useState, useEffect, useCallback } from 'react';
import { Modal, message, Button, Space } from 'antd';
import {
  PlusOutlined,
  CodeOutlined,
  FileTextOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { getScripts, deleteScript, validateScript } from '@/services/api';
import { PageHeader } from '@/components/common';
import ScriptTable from './components/ScriptTable';
import UploadModal from './components/UploadModal';
import EditModal from './components/EditModal';
import TemplateLibrary from './components/TemplateLibrary';
import './index.css';

export default function Scripts() {
  const [scripts, setScripts] = useState<any[]>([]);
  const [filteredScripts, setFilteredScripts] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploadModalVisible, setUploadModalVisible] = useState(false);
  const [editModalVisible, setEditModalVisible] = useState(false);
  const [templateModalVisible, setTemplateModalVisible] = useState(false);
  const [selectedScript, setSelectedScript] = useState<any>(null);
  const [searchText, setSearchText] = useState('');

  const loadScripts = useCallback(async () => {
    setLoading(true);
    try {
      const result = await getScripts();
      setScripts(result.scripts || []);
    } catch (error) {
      message.error('加载脚本列表失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadScripts();
  }, [loadScripts]);

  useEffect(() => {
    filterScripts();
  }, [scripts, searchText]);

  const filterScripts = () => {
    if (!searchText) {
      setFilteredScripts(scripts);
    } else {
      const lowerSearch = searchText.toLowerCase();
      setFilteredScripts(scripts.filter((s) =>
        s.path?.toLowerCase().includes(lowerSearch) ||
        s.name?.toLowerCase().includes(lowerSearch)
      ));
    }
  };

  const handleDelete = (scriptPath: string) => {
    Modal.confirm({
      title: '确认删除',
      content: `确定要删除脚本 ${scriptPath} 吗？`,
      okText: '确定',
      cancelText: '取消',
      onOk: async () => {
        try {
          await deleteScript(scriptPath);
          message.success('脚本删除成功');
          loadScripts();
        } catch (error: any) {
          message.error(error?.response?.data?.error || '删除失败');
        }
      },
    });
  };

  const handleEdit = (script: any) => {
    setSelectedScript(script);
    setEditModalVisible(true);
  };

  const handleUploadSuccess = () => {
    setUploadModalVisible(false);
    loadScripts();
    message.success('脚本上传成功');
  };

  const handleEditSuccess = () => {
    setEditModalVisible(false);
    loadScripts();
    message.success('脚本保存成功');
  };

  const handleUseTemplate = (templateContent: string, templatePath: string, isClone: boolean = false) => {
    setSelectedScript({ content: templateContent, path: isClone ? undefined : templatePath });
    setTemplateModalVisible(false);
    setUploadModalVisible(true);
  };

  const handleDownloadTemplate = (template: any) => {
    // 创建 Blob 并下载
    const blob = new Blob([template.content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = template.name + '.py';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    message.success(`模板 ${template.name} 已下载`);
  };

  return (
    <div className="scripts-page">
      <PageHeader
        icon={<CodeOutlined />}
        title="脚本管理"
        subtitle="管理自定义检测脚本"
        count={filteredScripts.length}
        countLabel="个脚本"
        extra={
          <Space size="middle">
            <Button
              icon={<ReloadOutlined />}
              onClick={loadScripts}
              loading={loading}
            >
              刷新
            </Button>
            <Button
              icon={<FileTextOutlined />}
              onClick={() => setTemplateModalVisible(true)}
            >
              模板库
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => {
                setSelectedScript(null);
                setUploadModalVisible(true);
              }}
              size="large"
              className="create-btn"
            >
              上传脚本
            </Button>
          </Space>
        }
      />

      <ScriptTable
        scripts={filteredScripts}
        loading={loading}
        searchText={searchText}
        onSearchChange={setSearchText}
        onEdit={handleEdit}
        onDelete={handleDelete}
      />

      <UploadModal
        visible={uploadModalVisible}
        script={selectedScript}
        onCancel={() => {
          setUploadModalVisible(false);
          setSelectedScript(null);
        }}
        onSuccess={handleUploadSuccess}
      />

      <EditModal
        visible={editModalVisible}
        script={selectedScript}
        onCancel={() => {
          setEditModalVisible(false);
          setSelectedScript(null);
        }}
        onSuccess={handleEditSuccess}
      />

      <TemplateLibrary
        visible={templateModalVisible}
        onClose={() => setTemplateModalVisible(false)}
        onUseTemplate={handleUseTemplate}
        onDownloadTemplate={handleDownloadTemplate}
      />
    </div>
  );
}
