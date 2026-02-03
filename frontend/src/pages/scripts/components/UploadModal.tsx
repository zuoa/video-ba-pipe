import React, { useState, useEffect } from 'react';
import { Modal, Form, Input, Select, Checkbox, Space, message, Tabs, Upload } from 'antd';
import { UploadOutlined, CodeOutlined, FileTextOutlined, InboxOutlined } from '@ant-design/icons';
import type { UploadChangeParam } from 'antd/es/upload';
import { createScript } from '@/services/api';
import CodeEditor from './CodeEditor';

const { Option } = Select;
const { TextArea } = Input;
const { TabPane } = Tabs;

export interface UploadModalProps {
  visible: boolean;
  script?: any;
  onCancel: () => void;
  onSuccess: () => void;
}

const UploadModal: React.FC<UploadModalProps> = ({
  visible,
  script,
  onCancel,
  onSuccess,
}) => {
  const [form] = Form.useForm();
  const [codeContent, setCodeContent] = useState('');
  const [createAlgorithm, setCreateAlgorithm] = useState(false);
  const [uploadMode, setUploadMode] = useState<'code' | 'file'>('code');
  const [fileList, setFileList] = useState<any[]>([]);

  useEffect(() => {
    if (visible) {
      if (script?.content) {
        setCodeContent(script.content);
        setUploadMode('code');
        if (script.path) {
          form.setFieldsValue({
            path: script.path,
          });
        }
      } else {
        form.resetFields();
        setCodeContent('');
        setCreateAlgorithm(false);
        setFileList([]);
        setUploadMode('code');
      }
    }
  }, [visible, script, form]);

  const handleFileChange = (info: UploadChangeParam) => {
    setFileList(info.fileList);

    if (info.fileList.length > 0) {
      const file = info.fileList[0].originFileObj;

      // 读取文件内容
      const reader = new FileReader();
      reader.onload = (e) => {
        const content = e.target?.result as string;
        setCodeContent(content);

        // 自动填充路径（如果为空）
        const pathValue = form.getFieldValue('path');
        if (!pathValue && file.name) {
          form.setFieldsValue({ path: file.name });
        }
      };
      reader.readAsText(file);
    } else {
      setCodeContent('');
    }
  };

  const beforeUpload = (file: File) => {
    if (!file.name.endsWith('.py')) {
      message.error('只能上传 .py 文件');
      return Upload.LIST_IGNORE;
    }
    return false; // 阻止自动上传
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();

      if (!codeContent.trim()) {
        message.error(uploadMode === 'code' ? '请输入脚本内容' : '请上传脚本文件');
        return;
      }

      await createScript({
        path: values.path,
        content: codeContent,
        create_algorithm: createAlgorithm,
      });

      onSuccess();
    } catch (error: any) {
      console.error('Upload error:', error);
      if (error?.errorFields) {
        return; // 表单验证错误
      }
      message.error(error?.response?.data?.error || '上传失败');
    }
  };

  const handleCancel = () => {
    form.resetFields();
    setCodeContent('');
    setCreateAlgorithm(false);
    onCancel();
  };

  return (
    <Modal
      title={
        <Space>
          <div className="modal-icon">
            <UploadOutlined />
          </div>
          <span>{script?.content ? '使用模板创建脚本' : '上传脚本'}</span>
        </Space>
      }
      open={visible}
      onCancel={handleCancel}
      onOk={handleSubmit}
      width={900}
      okText="创建"
      cancelText="取消"
      className="upload-modal"
      centered
    >
      <Form
        form={form}
        layout="vertical"
      >
        <Form.Item
          label="脚本路径"
          name="path"
          rules={[{ required: true, message: '请输入脚本路径' }]}
          help="相对于 app/user_scripts/ 的路径，例如: my_detector.py 或 detectors/my_detector.py"
        >
          <Input
            prefix={<CodeOutlined />}
            placeholder="my_detector.py"
            size="large"
          />
        </Form.Item>

        <Tabs
          activeKey={uploadMode}
          onChange={(key) => setUploadMode(key as 'code' | 'file')}
          items={[
            {
              key: 'code',
              label: (
                <span>
                  <CodeOutlined />
                  在线编辑
                </span>
              ),
              children: (
                <Form.Item label="脚本内容" required>
                  <CodeEditor
                    value={codeContent}
                    onChange={setCodeContent}
                    height={400}
                  />
                </Form.Item>
              ),
            },
            {
              key: 'file',
              label: (
                <span>
                  <FileTextOutlined />
                  文件上传
                </span>
              ),
              children: (
                <Form.Item label="上传 .py 文件" required>
                  <Upload.Dragger
                    fileList={fileList}
                    onChange={handleFileChange}
                    beforeUpload={beforeUpload}
                    accept=".py"
                    maxCount={1}
                  >
                    <p className="ant-upload-drag-icon">
                      <InboxOutlined />
                    </p>
                    <p className="ant-upload-text">点击或拖拽文件到此区域上传</p>
                    <p className="ant-upload-hint">
                      支持 .py 文件，文件将自动读取并填充到编辑器
                    </p>
                  </Upload.Dragger>
                  {codeContent && (
                    <div style={{ marginTop: 16 }}>
                      <p>文件内容预览：</p>
                      <CodeEditor
                        value={codeContent}
                        onChange={setCodeContent}
                        height={300}
                      />
                    </div>
                  )}
                </Form.Item>
              ),
            },
          ]}
        />

        <Form.Item>
          <Checkbox
            checked={createAlgorithm}
            onChange={(e) => setCreateAlgorithm(e.target.checked)}
          >
            同时创建算法记录（可直接在任务中使用）
          </Checkbox>
        </Form.Item>
      </Form>
    </Modal>
  );
};

export default UploadModal;
