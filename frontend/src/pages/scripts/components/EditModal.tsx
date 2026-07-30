import React, { useState, useEffect } from 'react';
import { Form, Input, Checkbox, Space, Button, message, Spin } from 'antd';
import { CheckOutlined, SaveOutlined } from '@ant-design/icons';
import { getScript, updateScript, validateScript } from '@/services/api';
import CodeEditor from './CodeEditor';
import ValidationModal from './ValidationModal';
import AppModal from '@/components/common/AppModal';

export interface EditModalProps {
  visible: boolean;
  script?: any;
  onCancel: () => void;
  onSuccess: () => void;
}

const EditModal: React.FC<EditModalProps> = ({
  visible,
  script,
  onCancel,
  onSuccess,
}) => {
  const [form] = Form.useForm();
  const [codeContent, setCodeContent] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [validating, setValidating] = useState(false);
  const [createVersion, setCreateVersion] = useState(true);
  const [validationModalVisible, setValidationModalVisible] = useState(false);
  const [validationResult, setValidationResult] = useState<any>(null);

  useEffect(() => {
    if (visible && script?.path) {
      loadScriptContent();
    }
  }, [visible, script]);

  const loadScriptContent = async () => {
    setLoading(true);
    try {
      const result = await getScript(script.path);
      setCodeContent(result.script?.content || '');
      form.setFieldsValue({
        changelog: '',
      });
    } catch (error) {
      message.error('加载脚本内容失败');
    } finally {
      setLoading(false);
    }
  };

  const handleValidate = async () => {
    setValidating(true);
    try {
      const result = await validateScript({ content: codeContent });
      if (result.success) {
        setValidationResult(result.validation);
        setValidationModalVisible(true);
      }
    } catch (error: any) {
      message.error('验证失败');
    } finally {
      setValidating(false);
    }
  };

  const handleSave = async () => {
    const changelog = form.getFieldValue('changelog') || '';

    setSaving(true);
    try {
      await updateScript(script.path, {
        content: codeContent,
        changelog: changelog,
        create_version: createVersion,
      });
      onSuccess();
    } catch (error: any) {
      message.error(error?.response?.data?.error || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    form.resetFields();
    setCodeContent('');
    setCreateVersion(true);
    onCancel();
  };

  return (
    <>
      <AppModal
        title="编辑脚本"
        description={script?.path}
        size="xl"
        open={visible}
        onCancel={handleCancel}
        footer={
          <Space>
            <Button onClick={handleCancel} disabled={saving || validating}>取消</Button>
            <Button
              icon={<CheckOutlined />}
              onClick={handleValidate}
              disabled={!codeContent.trim() || saving}
              loading={validating}
            >
              验证
            </Button>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              onClick={handleSave}
              loading={saving}
              disabled={!codeContent.trim() || validating}
            >
              保存
            </Button>
          </Space>
        }
        className="edit-modal"
        closable={!saving && !validating}
        keyboard={!saving && !validating}
        centered
      >
        <Spin spinning={loading}>
          <Form form={form} layout="vertical">
            <Form.Item label="脚本内容" required>
              <CodeEditor
                value={codeContent}
                onChange={setCodeContent}
                height={500}
              />
            </Form.Item>

            <Form.Item
              label="更新说明"
              name="changelog"
              help="描述这次更新做了什么（可选）"
            >
              <Input
                placeholder="例如: 修复了检测精度问题，优化了性能"
                size="large"
              />
            </Form.Item>

            <Form.Item>
              <Checkbox
                checked={createVersion}
                onChange={(e) => setCreateVersion(e.target.checked)}
              >
                创建版本备份
              </Checkbox>
            </Form.Item>
          </Form>
        </Spin>
      </AppModal>

      <ValidationModal
        visible={validationModalVisible}
        validation={validationResult}
        onClose={() => setValidationModalVisible(false)}
      />
    </>
  );
};

export default EditModal;
