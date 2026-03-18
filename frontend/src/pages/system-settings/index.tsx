import React, { useEffect, useState } from 'react';
import { Alert, Button, Card, Form, Input, InputNumber, Switch, message, Spin } from 'antd';
import { SettingOutlined, SaveOutlined } from '@ant-design/icons';
import { PageHeader } from '@/components/common';
import { getVlConfig, updateVlConfig } from '@/services/api';
import './index.css';

const SystemSettingsPage: React.FC = () => {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const loadConfig = async () => {
    setLoading(true);
    try {
      const response = await getVlConfig();
      form.setFieldsValue({
        enabled: response?.config?.enabled ?? false,
        base_url: response?.config?.base_url || '',
        model_name: response?.config?.model_name || '',
        api_key: response?.config?.api_key || '',
        timeout_seconds: response?.config?.timeout_seconds || 30,
      });
    } catch (error: any) {
      message.error(`加载 VL 配置失败: ${error.message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadConfig();
  }, []);

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      await updateVlConfig(values);
      message.success('VL 配置已保存');
      await loadConfig();
    } catch (error: any) {
      if (error?.errorFields) {
        return;
      }
      message.error(`保存失败: ${error.message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="system-settings-page">
      <PageHeader
        icon={<SettingOutlined />}
        title="系统设置"
        subtitle="统一管理系统级配置项，当前已开放 VL 核验服务配置。"
        extra={
          <Button
            type="primary"
            icon={<SaveOutlined />}
            loading={saving}
            onClick={handleSave}
          >
            保存配置
          </Button>
        }
      />

      {loading ? (
        <div className="system-settings-loading">
          <Spin size="large" />
        </div>
      ) : (
        <Card className="system-settings-card" title="视觉语言（VL）核验">
          <Alert
            type="info"
            showIcon
            className="system-settings-alert"
            message="系统设置是通用配置模块；当前这一组配置用于告警输出节点的 VL 二次核验能力。"
          />

          <Form form={form} layout="vertical">
            <Form.Item
              label="启用全局 VL 服务"
              name="enabled"
              valuePropName="checked"
            >
              <Switch />
            </Form.Item>

            <Form.Item
              label="BASE URL"
              name="base_url"
              extra="填写 OpenAI 兼容接口的基础地址，通常应包含 /v1。"
            >
              <Input placeholder="例如: https://your-host/v1" />
            </Form.Item>

            <Form.Item
              label="Model Name"
              name="model_name"
            >
              <Input placeholder="例如: gpt-4.1-mini" />
            </Form.Item>

            <Form.Item
              label="API Key"
              name="api_key"
            >
              <Input.Password placeholder="请输入调用密钥" />
            </Form.Item>

            <Form.Item
              label="请求超时（秒）"
              name="timeout_seconds"
            >
              <InputNumber min={3} max={120} style={{ width: '100%' }} />
            </Form.Item>
          </Form>
        </Card>
      )}
    </div>
  );
};

export default SystemSettingsPage;
