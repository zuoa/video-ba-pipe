import React, { useEffect, useState } from 'react';
import { Alert, Card, Form, Input, InputNumber, Progress, Select, Switch, Tabs, Tag, message, Spin } from 'antd';
import Button from '@/components/common/AppButton';
import {
  ApiOutlined,
  DatabaseOutlined,
  HddOutlined,
  KeyOutlined,
  NotificationOutlined,
  SafetyCertificateOutlined,
  SaveOutlined,
  SendOutlined,
  SettingOutlined,
  SyncOutlined,
  ThunderboltOutlined,
  VideoCameraOutlined,
  GlobalOutlined,
} from '@ant-design/icons';
import { PageHeader } from '@/components/common';
import {
  getSourceRotationConfig,
  getInferenceResourceConfig,
  getRecordingStorageConfig,
  getOpsNotificationConfig,
  getPublicMediaConfig,
  getRabbitMqConfig,
  getVlConfig,
  RecordingStorageUsage,
  InferenceResourceResponse,
  PublicMediaConfig,
  testOpsNotificationConfig,
  testRabbitMqConfig,
  updateSourceRotationConfig,
  updateInferenceResourceConfig,
  updateRecordingStorageConfig,
  updateOpsNotificationConfig,
  updatePublicMediaConfig,
  updateRabbitMqConfig,
  updateVlConfig,
} from '@/services/api';
import ApiKeySettingsCard from './ApiKeySettingsCard';
import './index.css';

const SystemSettingsPage: React.FC = () => {
  const [vlForm] = Form.useForm();
  const [rotationForm] = Form.useForm();
  const [recordingForm] = Form.useForm();
  const [opsForm] = Form.useForm();
  const [publicMediaForm] = Form.useForm();
  const [inferenceForm] = Form.useForm();
  const [rabbitmqForm] = Form.useForm();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testingWebhook, setTestingWebhook] = useState(false);
  const [testingMq, setTestingMq] = useState(false);
  const [activeTabKey, setActiveTabKey] = useState('inference');
  const [eligibleSourceCount, setEligibleSourceCount] = useState(0);
  const [storageUsage, setStorageUsage] = useState<RecordingStorageUsage | null>(null);
  const [inferenceResource, setInferenceResource] = useState<InferenceResourceResponse | null>(null);
  const [publicMediaConfig, setPublicMediaConfig] = useState<PublicMediaConfig | null>(null);
  const recordingEnabled = Form.useWatch('recording_enabled', recordingForm) ?? false;
  const videoMaxGb = Form.useWatch('video_max_gb', recordingForm) ?? 20;
  const imageMaxGb = Form.useWatch('image_max_gb', recordingForm) ?? 10;
  const rotationEnabled = Form.useWatch('enabled', rotationForm) ?? false;
  const opsEnabled = Form.useWatch('enabled', opsForm) ?? false;
  const mediaSigningEnabled = Form.useWatch('sign_media_urls', publicMediaForm) ?? true;
  const alertGrowthEnabled = Form.useWatch('notify_alert_growth', opsForm) ?? true;
  const sharedInferenceEnabled = Form.useWatch('shared_inference_enabled', inferenceForm) ?? false;
  const inferenceAdmissionEnabled = Form.useWatch('inference_admission_enabled', inferenceForm) ?? false;
  const oomCircuitEnabled = Form.useWatch('oom_circuit_breaker_enabled', inferenceForm) ?? false;
  const rabbitmqEnabled = Form.useWatch('enabled', rabbitmqForm) ?? false;
  const batchSize = Form.useWatch('batch_size', rotationForm) ?? 20;
  const dwellSeconds = Form.useWatch('dwell_seconds', rotationForm) ?? 30;
  const estimatedBatches = eligibleSourceCount > 0
    ? Math.ceil(eligibleSourceCount / Math.max(1, batchSize))
    : 0;
  const estimatedCycleSeconds = estimatedBatches * dwellSeconds;
  const inferenceStatus = inferenceResource?.status;
  const inferenceCapabilities = inferenceResource?.capabilities || {};
  const effectiveInference = inferenceStatus?.effective_config || inferenceResource?.effective_config;
  const workerOnline = inferenceStatus?.worker_online ?? false;
  const sharedServiceRunning = inferenceStatus?.service_running ?? false;
  const inferenceMemory = inferenceStatus?.memory;
  const inferenceModels = inferenceStatus?.models || [];

  const loadConfig = async () => {
    setLoading(true);
    try {
      const [vlResponse, rotationResponse, recordingResponse, opsResponse, inferenceResponse, publicMediaResponse, rabbitmqResponse] = await Promise.all([
        getVlConfig(),
        getSourceRotationConfig(),
        getRecordingStorageConfig(),
        getOpsNotificationConfig(),
        getInferenceResourceConfig(),
        getPublicMediaConfig(),
        getRabbitMqConfig(),
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
      recordingForm.setFieldsValue(recordingResponse.config);
      setStorageUsage(recordingResponse.usage);
      opsForm.setFieldsValue(opsResponse.config);
      inferenceForm.setFieldsValue(inferenceResponse.config);
      publicMediaForm.setFieldsValue(publicMediaResponse.config);
      setPublicMediaConfig(publicMediaResponse.config);
      setInferenceResource(inferenceResponse);
      rabbitmqForm.setFieldsValue(rabbitmqResponse.config);
    } catch (error: any) {
      message.error(`加载系统配置失败: ${error.message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadConfig();
  }, []);

  useEffect(() => {
    let active = true;
    const timer = window.setInterval(() => {
      getInferenceResourceConfig()
        .then((response) => {
          if (active) setInferenceResource(response);
        })
        .catch(() => undefined);
    }, 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const handleSave = async () => {
    // 逐个校验，第一个出错的页签自动切过去，避免错误藏在其它页签里
    const sections = [
      { key: 'inference', validate: () => inferenceForm.validateFields() },
      { key: 'recording', validate: () => recordingForm.validateFields() },
      { key: 'publicMedia', validate: () => publicMediaForm.validateFields() },
      { key: 'rabbitmq', validate: () => rabbitmqForm.validateFields() },
      { key: 'ops', validate: () => opsForm.validateFields() },
      { key: 'rotation', validate: () => rotationForm.validateFields() },
      { key: 'vl', validate: () => vlForm.validateFields() },
    ];
    const results = await Promise.allSettled(sections.map((section) => section.validate()));
    const firstErrorIndex = results.findIndex((result) => result.status === 'rejected');
    if (firstErrorIndex >= 0) {
      setActiveTabKey(sections[firstErrorIndex].key);
      message.error('请完善当前页签中标红的必填项');
      return;
    }
    const [
      inferenceValues,
      recordingValues,
      publicMediaValues,
      rabbitmqValues,
      opsValues,
      rotationValues,
      vlValues,
    ] = results.map((result) => (result.status === 'fulfilled' ? result.value : undefined));
    try {
      setSaving(true);
      const [, , , , inferenceResponse] = await Promise.all([
        updateVlConfig(vlValues),
        updateSourceRotationConfig(rotationValues),
        updateRecordingStorageConfig(recordingValues),
        updateOpsNotificationConfig(opsValues),
        updateInferenceResourceConfig(inferenceValues),
        updatePublicMediaConfig(publicMediaValues),
        updateRabbitMqConfig(rabbitmqValues),
      ]);
      setInferenceResource(inferenceResponse);
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

  const handleTestWebhook = async () => {
    try {
      const values = await opsForm.validateFields(['webhook_url', 'secret']);
      setTestingWebhook(true);
      await testOpsNotificationConfig({ ...opsForm.getFieldsValue(), ...values });
      message.success('钉钉测试通知已发送');
    } catch (error: any) {
      if (error?.errorFields) return;
      message.error(`测试通知失败: ${error.message}`);
    } finally {
      setTestingWebhook(false);
    }
  };

  const handleTestRabbitMq = async () => {
    try {
      setTestingMq(true);
      const response = await testRabbitMqConfig(rabbitmqForm.getFieldsValue());
      if (response?.success) {
        message.success(response.message || 'RabbitMQ 连接正常');
      } else {
        message.error(response?.error || '测试连接失败');
      }
    } catch (error: any) {
      message.error(`测试连接失败: ${error.message || error.error || '未知错误'}`);
    } finally {
      setTestingMq(false);
    }
  };

  return (
    <div className="system-settings-page">
      <PageHeader
        icon={<SettingOutlined />}
        title="系统设置"
        subtitle="统一管理推理资源、录像存储、运维通知、视频轮转、API Key、VL 核验与消息队列配置。"
        extra={activeTabKey !== 'apiKeys' ? (
          <Button
            type="primary"
            icon={<SaveOutlined />}
            loading={saving}
            onClick={handleSave}
          >
            保存配置
          </Button>
        ) : undefined}
      />

      {loading ? (
        <div className="system-settings-loading">
          <Spin size="large" />
        </div>
      ) : (
        <Tabs
          className="system-settings-tabs"
          activeKey={activeTabKey}
          onChange={setActiveTabKey}
          items={[
            {
              key: 'inference',
              label: (<span><SafetyCertificateOutlined /> 推理资源保护</span>),
              children: (
                <Card
                  className="system-settings-card inference-resource-card"
                  title={<span><SafetyCertificateOutlined /> 推理资源保护</span>}
                  extra={<span className="inference-config-source">自动兼容 · {configSourceLabel(inferenceResource?.config_source)}</span>}
                >
                  <div className="inference-status-strip" aria-label="推理资源运行状态">
                    <InferenceStatusMetric
                      label="Worker"
                      value={workerOnline ? '在线' : '离线'}
                      tone={workerOnline ? 'healthy' : 'danger'}
                      detail={inferenceStatus?.platform || inferenceCapabilities.platform || '未知平台'}
                    />
                    <InferenceStatusMetric
                      label="共享服务"
                      value={sharedServiceRunning ? '运行中' : '未运行'}
                      tone={sharedServiceRunning ? 'healthy' : sharedInferenceEnabled ? 'warning' : 'neutral'}
                      detail={effectiveInference?.shared_inference_enabled ? `PID ${inferenceStatus?.service_pid || '—'}` : '当前未生效'}
                    />
                    <InferenceStatusMetric
                      label="共享模型"
                      value={`${inferenceStatus?.model_count || 0} 个`}
                      tone="neutral"
                      detail={`${inferenceModels.reduce((sum, model) => sum + (model.references || 0), 0)} 个引用`}
                    />
                    <InferenceStatusMetric
                      label="内存余量"
                      value={inferenceMemory ? formatMb(inferenceMemory.available_mb) : '暂无数据'}
                      tone={inferenceMemory && inferenceMemory.usage_percent >= 90 ? 'danger' : 'neutral'}
                      detail={inferenceMemory ? `Swap ${formatMb(inferenceMemory.swap_used_mb)}` : '等待 worker 心跳'}
                    />
                  </div>

                  <div className="inference-capability-row" aria-label="平台推理能力">
                    <span>平台能力</span>
                    <CapabilityTag supported={Boolean(inferenceCapabilities.shared_ultralytics)}>Ultralytics 共享</CapabilityTag>
                    <CapabilityTag supported={Boolean(inferenceCapabilities.memory_admission)}>内存准入</CapabilityTag>
                    <CapabilityTag supported={Boolean(inferenceCapabilities.oom_detection)}>OOM 检测</CapabilityTag>
                    <CapabilityTag supported={Boolean(inferenceCapabilities.rknn_shared)}>RKNN 共享</CapabilityTag>
                    {inferenceResource?.restart_required
                      ? <Tag color="red">需要重启 worker</Tag>
                      : inferenceResource?.config_pending
                        ? <Tag color="gold">等待 worker 应用</Tag>
                        : <Tag color="green">配置已生效</Tag>}
                  </div>

                  <Alert
                    type={inferenceStatus?.reconcile_error ? 'error' : !workerOnline ? 'warning' : inferenceResource?.config_pending ? 'info' : 'success'}
                    showIcon
                    className="system-settings-alert"
                    message={inferenceStatus?.reconcile_error
                      ? '共享推理服务应用失败'
                      : !workerOnline
                        ? '没有收到 worker 状态心跳'
                        : inferenceResource?.config_pending
                          ? '配置已保存，worker 正在自动应用'
                          : '推理资源保护配置已生效'}
                    description="阈值和熔断参数热更新；共享服务、队列或批量参数变化时，只重建 source host，视频解码保持运行。RKNN、ONNX 和直连 YOLO 按本地模型副本计算。"
                  />

                  <Form form={inferenceForm} layout="vertical">
                    <InferenceSectionTitle icon={<ThunderboltOutlined />} title="共享推理" description="相同 Ultralytics 模型只保留一个模型进程" />
                    <div className="system-settings-form-grid">
                      <Form.Item label="启用共享推理" name="shared_inference_enabled" valuePropName="checked" extra="平台不支持时自动降级，不阻止本地推理。">
                        <Switch />
                      </Form.Item>
                      <Form.Item label="请求队列长度" name="queue_size" rules={[{ required: sharedInferenceEnabled, message: '请输入队列长度' }]}>
                        <InputNumber min={1} max={64} precision={0} disabled={!sharedInferenceEnabled} style={{ width: '100%' }} />
                      </Form.Item>
                      <Form.Item label="最大批量" name="batch_max_size" rules={[{ required: sharedInferenceEnabled, message: '请输入最大批量' }]}>
                        <InputNumber min={1} max={64} precision={0} disabled={!sharedInferenceEnabled} style={{ width: '100%' }} />
                      </Form.Item>
                      <Form.Item label="批量等待（毫秒）" name="batch_wait_ms" rules={[{ required: sharedInferenceEnabled, message: '请输入批量等待时间' }]}>
                        <InputNumber min={0} max={1000} precision={1} disabled={!sharedInferenceEnabled} style={{ width: '100%' }} />
                      </Form.Item>
                      <Form.Item label="请求超时（秒）" name="request_timeout_seconds" rules={[{ required: sharedInferenceEnabled, message: '请输入请求超时' }]}>
                        <InputNumber min={1} max={1800} precision={1} disabled={!sharedInferenceEnabled} style={{ width: '100%' }} />
                      </Form.Item>
                      <Form.Item label="模型空闲回收（秒）" name="model_idle_seconds" rules={[{ required: sharedInferenceEnabled, message: '请输入空闲回收时间' }]}>
                        <InputNumber min={10} max={86400} precision={0} disabled={!sharedInferenceEnabled} style={{ width: '100%' }} />
                      </Form.Item>
                    </div>

                    <InferenceSectionTitle icon={<HddOutlined />} title="内存准入" description="在加载模型前保留系统安全水位，Swap 不计入可用容量" />
                    <div className="system-settings-form-grid">
                      <Form.Item label="启用内存准入" name="inference_admission_enabled" valuePropName="checked">
                        <Switch />
                      </Form.Item>
                      <Form.Item label="保留内存（MB）" name="system_reserve_mb" rules={[{ required: inferenceAdmissionEnabled, message: '请输入保留内存' }]}>
                        <InputNumber min={256} max={1048576} precision={0} disabled={!inferenceAdmissionEnabled} style={{ width: '100%' }} />
                      </Form.Item>
                      <Form.Item label="保留内存比例（%）" name="system_reserve_percent" extra="MB 与比例取较大值。" rules={[{ required: inferenceAdmissionEnabled, message: '请输入保留比例' }]}>
                        <InputNumber min={0} max={50} precision={1} disabled={!inferenceAdmissionEnabled} style={{ width: '100%' }} />
                      </Form.Item>
                      <Form.Item label="新模型预估（MB）" name="new_model_default_mb" rules={[{ required: inferenceAdmissionEnabled, message: '请输入模型预估内存' }]}>
                        <InputNumber min={128} max={1048576} precision={0} disabled={!inferenceAdmissionEnabled} style={{ width: '100%' }} />
                      </Form.Item>
                      <Form.Item label="模型内存余量（%）" name="model_memory_margin_percent" rules={[{ required: inferenceAdmissionEnabled, message: '请输入模型内存余量' }]}>
                        <InputNumber min={0} max={100} precision={1} disabled={!inferenceAdmissionEnabled} style={{ width: '100%' }} />
                      </Form.Item>
                    </div>

                    <InferenceSectionTitle icon={<SafetyCertificateOutlined />} title="OOM 熔断" description="模型或 source host 被系统终止后逐级退避，避免重启风暴" />
                    <div className="system-settings-form-grid">
                      <Form.Item label="启用 OOM 熔断" name="oom_circuit_breaker_enabled" valuePropName="checked" extra="非 Linux/cgroup 平台自动降级。">
                        <Switch />
                      </Form.Item>
                      <Form.Item label="熔断失败次数" name="oom_failure_threshold" rules={[{ required: oomCircuitEnabled, message: '请输入失败次数' }]}>
                        <InputNumber min={1} max={100} precision={0} disabled={!oomCircuitEnabled} style={{ width: '100%' }} />
                      </Form.Item>
                      <Form.Item label="熔断持续（秒）" name="oom_circuit_open_seconds" rules={[{ required: oomCircuitEnabled, message: '请输入熔断时间' }]}>
                        <InputNumber min={30} max={86400} precision={0} disabled={!oomCircuitEnabled} style={{ width: '100%' }} />
                      </Form.Item>
                      <Form.Item label="稳定恢复（秒）" name="oom_stable_reset_seconds" rules={[{ required: oomCircuitEnabled, message: '请输入恢复时间' }]}>
                        <InputNumber min={60} max={86400} precision={0} disabled={!oomCircuitEnabled} style={{ width: '100%' }} />
                      </Form.Item>
                      <Form.Item label="最大退避（秒）" name="oom_restart_backoff_max_seconds" rules={[{ required: oomCircuitEnabled, message: '请输入最大退避时间' }]}>
                        <InputNumber min={30} max={86400} precision={0} disabled={!oomCircuitEnabled} style={{ width: '100%' }} />
                      </Form.Item>
                    </div>
                  </Form>

                  {inferenceModels.length > 0 ? (
                    <div className="inference-model-list" aria-label="共享模型运行详情">
                      {inferenceModels.map((model, index) => (
                        <div className="inference-model-row" key={`${model.model_id ?? 'model'}-${model.pid ?? index}`}>
                          <span className={`inference-model-dot ${model.ready ? 'is-ready' : ''}`} />
                          <strong>模型 {model.model_id ?? '未知'}</strong>
                          <span>PSS {formatMb(model.pss_mb)}</span>
                          <span>{model.references || 0} 个引用</span>
                          <span>队列 {model.queue_depth ?? '—'}</span>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </Card>
              ),
            },
            {
              key: 'recording',
              label: (<span><VideoCameraOutlined /> 录像与存储</span>),
              children: (
                <Card
                  className="system-settings-card"
                  title={<span><VideoCameraOutlined /> 录像与存储保护</span>}
                >
                  <Alert
                    type={recordingEnabled ? 'warning' : 'success'}
                    showIcon
                    className="system-settings-alert"
                    message={recordingEnabled ? '录像已开启，会持续占用磁盘空间' : '录像默认关闭'}
                    description="配置保存后 worker 会自动重建相关缓冲区。媒体目录超过上限时按最老文件优先覆盖；磁盘低于安全水位时会提前回收。"
                  />

                  <Form form={recordingForm} layout="vertical">
                    <div className="system-settings-form-grid">
                      <Form.Item
                        label="启用告警录像"
                        name="recording_enabled"
                        valuePropName="checked"
                        extra="仅影响告警前后录像，告警图片仍会保存并受容量上限保护。"
                      >
                        <Switch />
                      </Form.Item>

                      <Form.Item
                        label="录像帧率（FPS）"
                        name="recording_fps"
                        rules={[{ required: recordingEnabled, message: '请输入录像帧率' }]}
                      >
                        <InputNumber min={1} max={30} precision={0} disabled={!recordingEnabled} style={{ width: '100%' }} />
                      </Form.Item>

                      <Form.Item
                        label="告警前录像（秒）"
                        name="pre_alert_seconds"
                        rules={[{ required: recordingEnabled, message: '请输入告警前录像时长' }]}
                      >
                        <InputNumber min={0} max={300} precision={0} disabled={!recordingEnabled} style={{ width: '100%' }} />
                      </Form.Item>

                      <Form.Item
                        label="告警后录像（秒）"
                        name="post_alert_seconds"
                        rules={[{ required: recordingEnabled, message: '请输入告警后录像时长' }]}
                      >
                        <InputNumber min={0} max={300} precision={0} disabled={!recordingEnabled} style={{ width: '100%' }} />
                      </Form.Item>

                      <Form.Item
                        label="录像目录上限（GB）"
                        name="video_max_gb"
                        extra="达到上限后回收到约 90%，避免频繁逐文件清理。"
                        rules={[{ required: true, message: '请输入录像容量上限' }]}
                      >
                        <InputNumber min={1} max={4096} precision={1} style={{ width: '100%' }} />
                      </Form.Item>

                      <Form.Item
                        label="自动停录像水位（%）"
                        name="stop_recording_percent"
                        extra="磁盘达到该使用率后，正在进行和后续录像都会停止。"
                        rules={[{ required: true, message: '请输入自动停录像水位' }]}
                      >
                        <InputNumber min={1} max={98} precision={1} style={{ width: '100%' }} />
                      </Form.Item>

                      <Form.Item
                        label="仅保留元数据水位（%）"
                        name="metadata_only_percent"
                        dependencies={['stop_recording_percent']}
                        extra="达到后不再写入告警图片、临时图片和录像。"
                        rules={[
                          { required: true, message: '请输入仅保留元数据水位' },
                          ({ getFieldValue }) => ({
                            validator(_, value) {
                              if (Number(value) > Number(getFieldValue('stop_recording_percent'))) {
                                return Promise.resolve();
                              }
                              return Promise.reject(new Error('必须高于自动停录像水位'));
                            },
                          }),
                        ]}
                      >
                        <InputNumber min={2} max={99} precision={1} style={{ width: '100%' }} />
                      </Form.Item>

                      <Form.Item
                        label="图片目录上限（GB）"
                        name="image_max_gb"
                        rules={[{ required: true, message: '请输入图片容量上限' }]}
                      >
                        <InputNumber min={1} max={4096} precision={1} style={{ width: '100%' }} />
                      </Form.Item>

                      <Form.Item
                        label="最低剩余空间（GB）"
                        name="min_free_gb"
                        extra="低于该水位时，即使尚未达到目录上限，也会优先淘汰最老录像和图片。"
                        rules={[{ required: true, message: '请输入最低剩余空间' }]}
                      >
                        <InputNumber min={1} max={4096} precision={1} style={{ width: '100%' }} />
                      </Form.Item>
                    </div>
                  </Form>

                  {storageUsage ? (
                    <div className="storage-usage-panel" aria-label="当前媒体存储用量">
                      <StorageUsageItem
                        label="录像"
                        usedBytes={storageUsage.video_bytes}
                        maxGb={videoMaxGb}
                      />
                      <StorageUsageItem
                        label="图片"
                        usedBytes={storageUsage.image_bytes}
                        maxGb={imageMaxGb}
                      />
                      <div className="storage-free-space">
                        <DatabaseOutlined />
                        磁盘使用率 {storageUsage.disk_used_percent.toFixed(1)}%，剩余 {formatBytes(storageUsage.disk_free_bytes)} / {formatBytes(storageUsage.disk_total_bytes)}
                        <span className={`storage-pressure-badge storage-pressure-${storageUsage.pressure_level}`}>
                          {pressureLevelLabel(storageUsage.pressure_level)}
                        </span>
                      </div>
                    </div>
                  ) : null}
                </Card>
              ),
            },
            {
              key: 'publicMedia',
              label: (<span><GlobalOutlined /> 公共媒体</span>),
              children: (
                <Card
                  className="system-settings-card"
                  title={<span><GlobalOutlined /> 公共访问与媒体链接</span>}
                  extra={(
                    <span className="inference-config-source">
                      {publicMediaConfig?.public_base_url_override ? '系统设置覆盖' : '环境变量回退'}
                    </span>
                  )}
                >
                  <Alert
                    type="info"
                    showIcon
                    className="system-settings-alert"
                    message="用于 Webhook、RabbitMQ 和告警 API 输出可直接访问的媒体地址"
                    description="节点未单独覆盖 Host 时使用这里的全局地址。开启签名后，告警图片和录像链接会携带过期时间与签名。"
                  />

                  <Form form={publicMediaForm} layout="vertical">
                    <div className="system-settings-form-grid">
                      <Form.Item
                        className="system-settings-field-span-2"
                        label="公共访问地址"
                        name="public_base_url_override"
                        rules={[{ type: 'url', message: '请输入有效的 HTTP/HTTPS 地址' }]}
                        extra={`例如 https://video.example.com；留空时继承环境变量。当前生效：${publicMediaConfig?.public_base_url || '仅相对路径'}`}
                      >
                        <Input placeholder="https://video.example.com" />
                      </Form.Item>

                      <Form.Item label="生成签名媒体 URL" name="sign_media_urls" valuePropName="checked">
                        <Switch />
                      </Form.Item>

                      <Form.Item
                        label="链接有效期（小时）"
                        name="media_url_ttl_hours"
                        rules={[{ required: mediaSigningEnabled, message: '请输入链接有效期' }]}
                      >
                        <InputNumber min={1} max={720} precision={0} disabled={!mediaSigningEnabled} style={{ width: '100%' }} />
                      </Form.Item>
                    </div>
                  </Form>
                </Card>
              ),
            },
            {
              key: 'rabbitmq',
              label: (<span><ApiOutlined /> 消息队列</span>),
              children: (
                <Card
                  className="system-settings-card"
                  title={<span><ApiOutlined /> 消息队列 (RabbitMQ)</span>}
                  extra={
                    <Button
                      icon={<ApiOutlined />}
                      loading={testingMq}
                      onClick={handleTestRabbitMq}
                    >
                      测试连接
                    </Button>
                  }
                >
                  <Alert
                    type={rabbitmqEnabled ? 'info' : 'warning'}
                    showIcon
                    className="system-settings-alert"
                    message={rabbitmqEnabled ? 'RabbitMQ 预警发布已启用' : 'RabbitMQ 预警发布未启用'}
                    description="启用后，告警会在写入数据库的同时推送到下方交换机。生产者只声明交换机、不声明消费队列；队列名仅作为消费端约定。"
                  />

                  <Form form={rabbitmqForm} layout="vertical">
                    <div className="system-settings-form-grid">
                      <Form.Item label="启用 RabbitMQ" name="enabled" valuePropName="checked" extra="关闭后不再尝试建链与发布。">
                        <Switch />
                      </Form.Item>

                      <Form.Item
                        label="主机地址"
                        name="host"
                        rules={[{ required: rabbitmqEnabled, message: '请输入主机地址' }]}
                      >
                        <Input placeholder="rabbitmq 或 10.0.4.15" disabled={!rabbitmqEnabled} />
                      </Form.Item>

                      <Form.Item
                        label="端口"
                        name="port"
                        rules={[{ required: rabbitmqEnabled, message: '请输入端口' }]}
                      >
                        <InputNumber min={1} max={65535} precision={0} disabled={!rabbitmqEnabled} style={{ width: '100%' }} />
                      </Form.Item>

                      <Form.Item
                        label="虚拟主机"
                        name="vhost"
                        rules={[{ required: rabbitmqEnabled, message: '请输入虚拟主机' }]}
                      >
                        <Input placeholder="/" disabled={!rabbitmqEnabled} />
                      </Form.Item>

                      <Form.Item
                        label="用户名"
                        name="username"
                        rules={[{ required: rabbitmqEnabled, message: '请输入用户名' }]}
                      >
                        <Input placeholder="admin" disabled={!rabbitmqEnabled} />
                      </Form.Item>

                      <Form.Item
                        label="密码"
                        name="password"
                        extra="留空则保留已保存的密码，不会清空。"
                      >
                        <Input.Password placeholder="请输入密码" autoComplete="new-password" />
                      </Form.Item>

                      <Form.Item
                        label="交换机名称"
                        name="alert_exchange"
                        rules={[{ required: rabbitmqEnabled, message: '请输入交换机名称' }]}
                      >
                        <Input placeholder="video_alerts" disabled={!rabbitmqEnabled} />
                      </Form.Item>

                      <Form.Item
                        label="交换机类型"
                        name="exchange_type"
                        rules={[{ required: rabbitmqEnabled, message: '请选择交换机类型' }]}
                      >
                        <Select
                          disabled={!rabbitmqEnabled}
                          options={[
                            { value: 'topic', label: 'topic（按节点/类型订阅）' },
                            { value: 'direct', label: 'direct（按固定 routing key）' },
                          ]}
                        />
                      </Form.Item>

                      <Form.Item
                        label="Routing Key"
                        name="alert_routing_key"
                        extra="仅 direct 模式使用；topic 模式自动生成 video.alert.{节点}.{类型}。"
                      >
                        <Input placeholder="alert" disabled={!rabbitmqEnabled} />
                      </Form.Item>

                      <Form.Item
                        label="队列名"
                        name="alert_queue"
                        extra="消费端声明的队列名，生产者不声明，仅作约定。"
                      >
                        <Input placeholder="video_alerts" disabled={!rabbitmqEnabled} />
                      </Form.Item>

                      <Form.Item
                        label="连接超时（秒）"
                        name="connection_timeout_seconds"
                        rules={[{ required: true, message: '请输入连接超时' }]}
                      >
                        <InputNumber min={1} max={300} precision={0} style={{ width: '100%' }} />
                      </Form.Item>
                    </div>
                  </Form>
                </Card>
              ),
            },
            {
              key: 'ops',
              label: (<span><NotificationOutlined /> 钉钉通知</span>),
              children: (
                <Card
                  className="system-settings-card"
                  title={<span><NotificationOutlined /> 钉钉运维通知</span>}
                  extra={
                    <Button
                      icon={<SendOutlined />}
                      loading={testingWebhook}
                      onClick={handleTestWebhook}
                    >
                      发送测试通知
                    </Button>
                  }
                >
                  <Alert
                    type={opsEnabled ? 'info' : 'warning'}
                    showIcon
                    className="system-settings-alert"
                    message={opsEnabled ? '运维通知已启用' : '运维通知未启用'}
                    description="支持磁盘水位变化、媒体清理失败和滑动时间窗内告警量异常。相同事件按冷却时间去重，避免通知风暴。"
                  />

                  <Form form={opsForm} layout="vertical">
                    <div className="system-settings-form-grid">
                      <Form.Item label="启用钉钉通知" name="enabled" valuePropName="checked">
                        <Switch />
                      </Form.Item>

                      <Form.Item label="磁盘水位通知" name="notify_disk_pressure" valuePropName="checked">
                        <Switch disabled={!opsEnabled} />
                      </Form.Item>

                      <Form.Item label="清理失败通知" name="notify_cleanup_failure" valuePropName="checked">
                        <Switch disabled={!opsEnabled} />
                      </Form.Item>

                      <Form.Item label="异常告警增长通知" name="notify_alert_growth" valuePropName="checked">
                        <Switch disabled={!opsEnabled} />
                      </Form.Item>

                      <Form.Item
                        className="system-settings-field-span-2"
                        label="钉钉机器人 Webhook"
                        name="webhook_url"
                        rules={[
                          { required: opsEnabled, message: '启用通知时必须填写 Webhook' },
                          { type: 'url', message: '请输入有效的 HTTPS URL' },
                        ]}
                        extra="仅接受钉钉官方 oapi.dingtalk.com/robot/send 地址；机器人关键词可设置为“VideoBA运维”。"
                      >
                        <Input placeholder="https://oapi.dingtalk.com/robot/send?access_token=..." />
                      </Form.Item>

                      <Form.Item
                        className="system-settings-field-span-2"
                        label="加签密钥（可选）"
                        name="secret"
                        extra="留空会保留已保存的密钥；建议在钉钉机器人安全设置中启用加签。"
                      >
                        <Input.Password placeholder="SEC..." autoComplete="new-password" />
                      </Form.Item>

                      <Form.Item
                        label="统计窗口（分钟）"
                        name="alert_growth_window_minutes"
                        rules={[{ required: opsEnabled && alertGrowthEnabled, message: '请输入统计窗口' }]}
                      >
                        <InputNumber min={1} max={1440} precision={0} disabled={!opsEnabled || !alertGrowthEnabled} style={{ width: '100%' }} />
                      </Form.Item>

                      <Form.Item
                        label="窗口告警阈值（条）"
                        name="alert_growth_threshold"
                        rules={[{ required: opsEnabled && alertGrowthEnabled, message: '请输入告警阈值' }]}
                      >
                        <InputNumber min={1} max={1000000} precision={0} disabled={!opsEnabled || !alertGrowthEnabled} style={{ width: '100%' }} />
                      </Form.Item>

                      <Form.Item
                        label="相同事件冷却（分钟）"
                        name="cooldown_minutes"
                        rules={[{ required: opsEnabled, message: '请输入通知冷却时间' }]}
                      >
                        <InputNumber min={1} max={1440} precision={0} disabled={!opsEnabled} style={{ width: '100%' }} />
                      </Form.Item>
                    </div>
                  </Form>
                </Card>
              ),
            },
            {
              key: 'rotation',
              label: (<span><SyncOutlined /> 视频轮转</span>),
              children: (
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
                      rules={[{ required: rotationEnabled, message: '请输入每批检测路数' }]}
                    >
                      <InputNumber min={1} precision={0} disabled={!rotationEnabled} style={{ width: '100%' }} />
                    </Form.Item>

                    <Form.Item
                      label="单批检测时长（秒）"
                      name="dwell_seconds"
                      extra="最短 10 秒；RTSP 建链和模型加载时间不计入检测时长。"
                      rules={[{ required: rotationEnabled, message: '请输入单批检测时长' }]}
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
              ),
            },
            {
              key: 'apiKeys',
              label: (<span><KeyOutlined /> API Key</span>),
              children: (
                <Card className="system-settings-card" title={<span><KeyOutlined /> API Key 管理</span>}>
                  <ApiKeySettingsCard />
                </Card>
              ),
            },
            {
              key: 'vl',
              label: (<span><SafetyCertificateOutlined /> VL 核验</span>),
              children: (
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
              ),
            },
          ]}
        />
      )}
    </div>
  );
};

const GIB = 1024 ** 3;

function formatMb(value?: number | null): string {
  if (!Number.isFinite(value)) return '—';
  const numericValue = Number(value);
  return numericValue >= 1024
    ? `${(numericValue / 1024).toFixed(1)} GB`
    : `${numericValue.toFixed(0)} MB`;
}

function configSourceLabel(source?: string): string {
  if (source === 'database') return '数据库配置';
  if (source === 'environment_initialized') return '环境默认已入库';
  if (source === 'environment_fallback') return '数据库不可用，环境回退';
  return '环境默认';
}

interface InferenceStatusMetricProps {
  label: string;
  value: string;
  detail: string;
  tone: 'healthy' | 'warning' | 'danger' | 'neutral';
}

const InferenceStatusMetric: React.FC<InferenceStatusMetricProps> = ({ label, value, detail, tone }) => (
  <div className={`inference-status-metric inference-status-${tone}`}>
    <span>{label}</span>
    <strong>{value}</strong>
    <small>{detail}</small>
  </div>
);

const CapabilityTag: React.FC<{ supported: boolean; children: React.ReactNode }> = ({ supported, children }) => (
  <Tag color={supported ? 'cyan' : 'default'}>{children} · {supported ? '支持' : '未支持'}</Tag>
);

const InferenceSectionTitle: React.FC<{
  icon: React.ReactNode;
  title: string;
  description: string;
}> = ({ icon, title, description }) => (
  <div className="inference-section-title">
    <span>{icon}</span>
    <div>
      <strong>{title}</strong>
      <small>{description}</small>
    </div>
  </div>
);

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 GB';
  return `${(bytes / GIB).toFixed(1)} GB`;
}

function pressureLevelLabel(level: RecordingStorageUsage['pressure_level']): string {
  if (level === 'metadata_only') return '仅保留元数据';
  if (level === 'recording_stopped') return '录像已暂停';
  return '正常';
}

interface StorageUsageItemProps {
  label: string;
  usedBytes: number;
  maxGb: number;
}

const StorageUsageItem: React.FC<StorageUsageItemProps> = ({ label, usedBytes, maxGb }) => {
  const maxBytes = Math.max(1, maxGb) * GIB;
  const percent = Math.min(100, Math.round((usedBytes / maxBytes) * 100));
  return (
    <div className="storage-usage-item">
      <div className="storage-usage-label">
        <span>{label}</span>
        <strong>{formatBytes(usedBytes)} / {maxGb} GB</strong>
      </div>
      <Progress percent={percent} size="small" status={percent >= 90 ? 'exception' : 'normal'} />
    </div>
  );
};

export default SystemSettingsPage;
