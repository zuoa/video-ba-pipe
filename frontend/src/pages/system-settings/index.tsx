import React, { useEffect, useState } from 'react';
import { Alert, Card, Form, Input, InputNumber, Switch, message, Spin } from 'antd';
import Button from '@/components/common/AppButton';
import { SettingOutlined, SaveOutlined, SyncOutlined } from '@ant-design/icons';
import { PageHeader } from '@/components/common';
import {
  getSourceRotationConfig,
  getVlConfig,
  updateSourceRotationConfig,
  updateVlConfig,
} from '@/services/api';
import './index.css';

const SystemSettingsPage: React.FC = () => {
  const [vlForm] = Form.useForm();
  const [rotationForm] = Form.useForm();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [eligibleSourceCount, setEligibleSourceCount] = useState(0);
  const rotationEnabled = Form.useWatch('enabled', rotationForm) ?? false;
  const batchSize = Form.useWatch('batch_size', rotationForm) ?? 20;
  const dwellSeconds = Form.useWatch('dwell_seconds', rotationForm) ?? 30;
  const estimatedBatches = eligibleSourceCount > 0
    ? Math.ceil(eligibleSourceCount / Math.max(1, batchSize))
    : 0;
  const estimatedCycleSeconds = estimatedBatches * dwellSeconds;

  const loadConfig = async () => {
    setLoading(true);
    try {
      const [vlResponse, rotationResponse] = await Promise.all([
        getVlConfig(),
        getSourceRotationConfig(),
      ]);
      vlForm.setFieldsValue({
        enabled: vlResponse?.config?.enabled ?? false,
        base_url: vlResponse?.config?.base_url || '',
        model_name: vlResponse?.config?.model_name || '',
        api_key: vlResponse?.config?.api_key || '',
        timeout_seconds: vlResponse?.config?.timeout_seconds || 30,
      });
      rotationForm.setFieldsValue({
        enabled: rotationResponse?.config?.enabled ?? false,
        batch_size: rotationResponse?.config?.batch_size || 20,
        dwell_seconds: rotationResponse?.config?.dwell_seconds || 30,
      });
      setEligibleSourceCount(rotationResponse?.eligible_source_count || 0);
    } catch (error: any) {
      message.error(`加载系统配置失败: ${error.message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadConfig();
  }, []);

  const handleSave = async () => {
    try {
      const [vlValues, rotationValues] = await Promise.all([
        vlForm.validateFields(),
        rotationForm.validateFields(),
      ]);
      setSaving(true);
      await Promise.all([
        updateVlConfig(vlValues),
        updateSourceRotationConfig(rotationValues),
      ]);
      message.success('系统配置已保存');
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
        subtitle="统一管理视频轮转检测与 VL 核验服务配置。"
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
        <div className="system-settings-grid">
          <Card
            className="system-settings-card"
            title={<span><SyncOutlined /> 视频轮转检测</span>}
          >
            <Alert
              type="warning"
              showIcon
              className="system-settings-alert"
              message="轮转会形成检测盲区"
              description="只有已启用且绑定活动工作流的视频源参与。批次时长从首帧和工作流就绪后开始计算。"
            />

            <Form form={rotationForm} layout="vertical">
              <Form.Item
                label="启用轮转检测"
                name="enabled"
                valuePropName="checked"
              >
                <Switch />
              </Form.Item>

              <Form.Item
                label="每批检测路数"
                name="batch_size"
                rules={[{ required: true, message: '请输入每批检测路数' }]}
              >
                <InputNumber min={1} precision={0} disabled={!rotationEnabled} style={{ width: '100%' }} />
              </Form.Item>

              <Form.Item
                label="单批检测时长（秒）"
                name="dwell_seconds"
                extra="最短 10 秒；RTSP 建链和模型加载时间不计入检测时长。"
                rules={[{ required: true, message: '请输入单批检测时长' }]}
              >
                <InputNumber min={10} precision={0} disabled={!rotationEnabled} style={{ width: '100%' }} />
              </Form.Item>

              <div className="rotation-estimate">
                <span>当前符合条件：{eligibleSourceCount} 路</span>
                <span>预计批次数：{estimatedBatches} 批</span>
                <strong>完整轮转约 {estimatedCycleSeconds} 秒</strong>
              </div>
            </Form>
          </Card>

          <Card className="system-settings-card" title="视觉语言（VL）核验">
            <Alert
              type="info"
              showIcon
              className="system-settings-alert"
              message="用于告警输出节点的 VL 二次核验能力。"
            />

            <Form form={vlForm} layout="vertical">
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
        </div>
      )}
    </div>
  );
};

export default SystemSettingsPage;
