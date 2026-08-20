import React, { useEffect, useMemo, useState } from 'react';
import { Alert, Card, Form, Input, InputNumber, Progress, Select, Switch, Tabs, Tag, Typography, message, Spin } from 'antd';
import type { FormInstance } from 'antd';
import Button from '@/components/common/AppButton';
import {
  ApiOutlined,
  ApartmentOutlined,
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
  CopyOutlined,
  DeleteOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import { PageHeader } from '@/components/common';
import { copyToClipboard } from '@/utils/clipboard';
import {
  getSourceRotationConfig,
  getVideoDecodeConfig,
  getSystemInfo,
  getInferenceResourceConfig,
  getRecordingStorageConfig,
  getOpsNotificationConfig,
  getPublicMediaConfig,
  getMessageQueueConfig,
  getVlConfig,
  RecordingStorageUsage,
  InferenceResourceResponse,
  PublicMediaConfig,
  AlertDeliveryStats,
  SystemInfo,
  testOpsNotificationConfig,
  testObjectStorageConfig,
  testMessageQueueConfig,
  updateSourceRotationConfig,
  updateVideoDecodeConfig,
  updateInferenceResourceConfig,
  updateRecordingStorageConfig,
  updateOpsNotificationConfig,
  updatePublicMediaConfig,
  retryFailedAlertDeliveries,
  updateMessageQueueConfig,
  updateVlConfig,
} from '@/services/api';
import ApiKeySettingsCard from './ApiKeySettingsCard';
import LicenseSettingsCard from './LicenseSettingsCard';
import './index.css';

const validateAndGetAllFields = async (form: FormInstance) => {
  await form.validateFields();
  // Tabs are rendered lazily. `true` includes values loaded before a tab's fields mount.
  return form.getFieldsValue(true);
};

const NODE_ID_SOURCE_LABELS: Record<string, string> = {
  environment: '环境变量',
  mac: 'MAC 地址',
  persistent_file: '持久化文件',
  uuid: '自动 UUID',
  hostname: '主机名回退',
};

const EMPTY_HTTP_HEADERS: Array<{ name?: string; value?: string }> = [];

const buildHttpReceiverPrompt = ({
  endpointUrl,
  nodeId,
  customHeaderNames,
  mediaDeliveryMode,
}: {
  endpointUrl: string;
  nodeId: string;
  customHeaderNames: string[];
  mediaDeliveryMode: string;
}) => {
  const signatureInstruction = `HMAC-SHA256 校验协议必须与发送端完全一致：
   - 读取 X-VideoBA-Node-Id、X-VideoBA-Timestamp、X-VideoBA-Nonce、X-VideoBA-Event-Id、X-VideoBA-Event-Type、X-VideoBA-Test 和 X-VideoBA-Signature；保留各请求头的原始字符串值。
   - 先读取未经重新序列化的原始 HTTP 请求体字节 raw_body，再解析 JSON；body_sha256 = SHA256(raw_body) 的小写十六进制。
   - X-VideoBA-Test 只允许小写 true 或 false。canonical = node_id + "\\n" + timestamp + "\\n" + nonce + "\\n" + event_id + "\\n" + event_type + "\\n" + test_marker + "\\n" + body_sha256，其中 event_type 和 test_marker 分别是 X-VideoBA-Event-Type、X-VideoBA-Test 的原始值。
   - expected = HMAC-SHA256(key=UTF8(<HMAC_SHARED_SECRET>), message=UTF8(canonical)) 的小写十六进制。
   - X-VideoBA-Signature 的格式为 sha256=<expected>；使用常量时间比较，禁止普通字符串比较。
   - timestamp 是 Unix 秒，只接受服务器当前时间前后 300 秒；nonce 必须非空并至少缓存 10 分钟，重复 nonce 返回 409。
   - 先完成签名校验，再登记 nonce，避免伪造请求占满防重放缓存；发送端和接收端都要同步系统时间。
   - X-VideoBA-Node-Id 必须等于已登记节点 ${nodeId || '<CURRENT_NODE_ID>'}；JSON 中的 node_id、event_id、event_type 必须分别等于对应请求头。JSON test 严格为布尔值 true 时 X-VideoBA-Test 必须为 true，否则必须为 false。任何重复字段不一致都要拒绝，不能用未核验的请求头分流。签名、时间戳或节点校验失败返回 401。`;
  const customHeaders = customHeaderNames.length > 0
    ? `还要校验这些自定义请求头（值从安全配置读取）：${customHeaderNames.map((name) => `${name}: <${name.toUpperCase().replace(/[^A-Z0-9]+/g, '_')}_VALUE>`).join('；')}。`
    : '没有额外的自定义请求头。';
  const commonExample = {
    event_id: 'box-01-42:alert-created',
    event_type: 'alert.created',
    test: false,
    source: 'video-ba-pipe',
    node_id: nodeId || 'box-01',
    external_alert_id: `${nodeId || 'box-01'}-42`,
    alert_id: 42,
    alert_type: 'person',
    alert_level: 'warning',
    alert_message: '检测到人员',
  };
  const example = {
    ...commonExample,
    media_delivery_mode: mediaDeliveryMode,
    media: {
      status: mediaDeliveryMode === 'object_storage' ? 'pending' : 'ready',
      image: mediaDeliveryMode === 'inline'
        ? { kind: 'inline', content_type: 'image/jpeg', encoding: 'base64', data: '<BASE64_DATA>' }
        : mediaDeliveryMode === 'url'
          ? { kind: 'url', url: 'https://video.example.com/api/alerts/image/42' }
          : null,
    },
  };
  const mediaContracts = {
    url: {
      media_delivery_mode: 'url',
      media: {
        status: 'ready',
        image: { kind: 'url', url: 'https://video.example.com/api/image/frames/alert.jpg' },
      },
    },
    inline: {
      media_delivery_mode: 'inline',
      media: {
        status: 'ready',
        image: {
          kind: 'inline',
          content_type: 'image/jpeg',
          encoding: 'base64',
          size_bytes: 123456,
          encoded_size_bytes: 164608,
          data: '<BASE64_DATA>',
        },
      },
    },
    object_storage_created: {
      event_type: 'alert.created',
      media_delivery_mode: 'object_storage',
      media: { status: 'pending', image: null },
    },
    object_storage_ready: {
      event_type: 'alert.media.ready',
      media_delivery_mode: 'object_storage',
      media: {
        status: 'ready',
        image: {
          kind: 'url',
          url: 'https://object-storage.example.com/signed/alert.jpg',
          object_key: 'alerts/box-01/42.jpg',
          expires_at: '2030-01-01T00:00:00',
        },
      },
    },
  };
  return `请在当前项目中实现 VideoBA 告警接收端 API，要求如下：

1. 创建 POST ${endpointUrl || '<HTTP_ENDPOINT_URL>'}，接收 Content-Type: application/json；不要依赖重定向。
2. 所有请求都必须使用下面定义的 HMAC-SHA256 请求签名，不发送 Authorization。共享密钥从环境变量或安全配置读取，示例中统一写作 <HMAC_SHARED_SECRET>。${customHeaders}
3. ${signatureInstruction}
4. 只有在验签及请求头/JSON 一致性校验全部成功后才能分流。仅当 event_type=system.test 且 test=true 时作为连通性测试；两者不一致时拒绝请求，不要按未签名或未核验的值分类。
5. 请求体示例：
${JSON.stringify(example, null, 2)}
6. 当前发送端选择的媒体模式是 ${mediaDeliveryMode}，但接收端必须按 media_delivery_mode 和 media.image.kind 明确实现以下三种协议分支：
${JSON.stringify(mediaContracts, null, 2)}
   - inline：图片已经随本次 JSON 请求放在 media.image.data 中。只在 encoding=base64 且 content_type 是允许的图片类型时解码并落盘；校验解码后的字节数与 size_bytes 一致。禁止再根据 alert_image、alert_image_url 或本地相对路径反向请求发送端。
   - url：从 media.image.url 获取图片；设置连接/读取超时、响应大小上限，并采取 SSRF 防护。不要把 URL 字符串当作 Base64。
   - object_storage：alert.created 且 media.status=pending 时先持久化文字告警；后续用 external_alert_id 关联 alert.media.ready，再处理其中 media.image.url。不要把 pending 当成图片丢失。
   media 字段是媒体处理的权威来源；alert_image、alert_image_ori 等顶层本地路径只作为兼容元数据，接收端不得将其拼接成回源地址。未知 media_delivery_mode 或 kind 返回 422 并记录原因；其它新增可选字段可以忽略。
7. 使用 event_id 建立唯一约束并实现幂等；同一 event_id 再次到达应返回原成功结果或其它 2xx，而不是唯一约束错误。使用 external_alert_id 关联同一告警的 created 与 media.ready 事件。
8. 只有在事件已可靠接收或持久化后才返回任意 2xx。校验失败返回 4xx，临时故障返回 5xx；发送端会按至少一次语义重试所有非 2xx、超时和网络错误。
9. 生产环境必须使用 HTTPS；HMAC 提供真实性和完整性，但不加密请求内容。
10. 请给出可直接运行的实现、依赖安装命令、环境变量示例、数据库建表/迁移，以及测试正常事件、重复事件、测试事件、过期时间戳、重复 nonce、请求体被修改、事件类型头被修改、测试标记头被修改、重复字段不一致和签名失败的自动化测试。`;
};

const SystemSettingsPage: React.FC = () => {
  const [vlForm] = Form.useForm();
  const [rotationForm] = Form.useForm();
  const [videoDecodeForm] = Form.useForm();
  const [recordingForm] = Form.useForm();
  const [opsForm] = Form.useForm();
  const [publicMediaForm] = Form.useForm();
  const [inferenceForm] = Form.useForm();
  const [messageQueueForm] = Form.useForm();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testingWebhook, setTestingWebhook] = useState(false);
  const [testingMq, setTestingMq] = useState(false);
  const [testingObjectStorage, setTestingObjectStorage] = useState(false);
  const [retryingDeliveries, setRetryingDeliveries] = useState(false);
  const [activeTabKey, setActiveTabKey] = useState('inference');
  const [eligibleSourceCount, setEligibleSourceCount] = useState(0);
  const [storageUsage, setStorageUsage] = useState<RecordingStorageUsage | null>(null);
  const [inferenceResource, setInferenceResource] = useState<InferenceResourceResponse | null>(null);
  const [publicMediaConfig, setPublicMediaConfig] = useState<PublicMediaConfig | null>(null);
  const [deliveryStats, setDeliveryStats] = useState<AlertDeliveryStats>({ pending: 0, processing: 0, retrying: 0, failed: 0 });
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null);
  const [videoDecodeConfigSource, setVideoDecodeConfigSource] = useState<'database' | 'environment'>('environment');
  const recordingEnabled = Form.useWatch('recording_enabled', recordingForm) ?? false;
  const videoMaxGb = Form.useWatch('video_max_gb', recordingForm) ?? 20;
  const imageMaxGb = Form.useWatch('image_max_gb', recordingForm) ?? 10;
  const rotationEnabled = Form.useWatch('enabled', rotationForm) ?? false;
  const decodeKeyframesOnly = Form.useWatch('decode_keyframes_only', videoDecodeForm) ?? false;
  const opsEnabled = Form.useWatch('enabled', opsForm) ?? false;
  const mediaSigningEnabled = Form.useWatch('sign_media_urls', publicMediaForm) ?? true;
  const mediaDeliveryMode = Form.useWatch('delivery_mode', publicMediaForm) ?? 'url';
  const alertGrowthEnabled = Form.useWatch('notify_alert_growth', opsForm) ?? true;
  const sharedInferenceEnabled = Form.useWatch('shared_inference_enabled', inferenceForm) ?? false;
  const inferenceAdmissionEnabled = Form.useWatch('inference_admission_enabled', inferenceForm) ?? false;
  const oomCircuitEnabled = Form.useWatch('oom_circuit_breaker_enabled', inferenceForm) ?? false;
  const messageQueueEnabled = Form.useWatch('enabled', messageQueueForm) ?? false;
  const messageQueueProvider = Form.useWatch('provider', messageQueueForm) ?? 'mqtt';
  const httpEndpointUrl = Form.useWatch(['http', 'endpoint_url'], messageQueueForm) ?? '';
  const httpCustomHeaders = Form.useWatch(['http', 'custom_headers'], messageQueueForm) ?? EMPTY_HTTP_HEADERS;
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
  const httpReceiverPrompt = useMemo(() => buildHttpReceiverPrompt({
    endpointUrl: httpEndpointUrl,
    nodeId: systemInfo?.node_id || '',
    customHeaderNames: (Array.isArray(httpCustomHeaders) ? httpCustomHeaders : [])
      .map((header: any) => String(header?.name || '').trim())
      .filter(Boolean),
    mediaDeliveryMode,
  }), [httpCustomHeaders, httpEndpointUrl, mediaDeliveryMode, systemInfo?.node_id]);

  const loadConfig = async () => {
    setLoading(true);
    try {
      const [vlResponse, rotationResponse, videoDecodeResponse, recordingResponse, opsResponse, inferenceResponse, publicMediaResponse, messageQueueResponse, systemInfoResponse] = await Promise.all([
        getVlConfig(),
        getSourceRotationConfig(),
        getVideoDecodeConfig(),
        getRecordingStorageConfig(),
        getOpsNotificationConfig(),
        getInferenceResourceConfig(),
        getPublicMediaConfig(),
        getMessageQueueConfig(),
        getSystemInfo(),
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
      videoDecodeForm.setFieldsValue(videoDecodeResponse.config);
      setVideoDecodeConfigSource(videoDecodeResponse.config_source);
      recordingForm.setFieldsValue(recordingResponse.config);
      setStorageUsage(recordingResponse.usage);
      opsForm.setFieldsValue(opsResponse.config);
      inferenceForm.setFieldsValue(inferenceResponse.config);
      publicMediaForm.setFieldsValue(publicMediaResponse.config);
      setPublicMediaConfig(publicMediaResponse.config);
      setDeliveryStats(publicMediaResponse.delivery_stats || { pending: 0, processing: 0, retrying: 0, failed: 0 });
      setInferenceResource(inferenceResponse);
      messageQueueForm.setFieldsValue(messageQueueResponse.config);
      setSystemInfo(systemInfoResponse);
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
      { key: 'inference', validate: () => validateAndGetAllFields(inferenceForm) },
      { key: 'videoDecode', validate: () => validateAndGetAllFields(videoDecodeForm) },
      { key: 'recording', validate: () => validateAndGetAllFields(recordingForm) },
      { key: 'publicMedia', validate: () => validateAndGetAllFields(publicMediaForm) },
      { key: 'messageQueue', validate: () => validateAndGetAllFields(messageQueueForm) },
      { key: 'ops', validate: () => validateAndGetAllFields(opsForm) },
      { key: 'rotation', validate: () => validateAndGetAllFields(rotationForm) },
      { key: 'vl', validate: () => validateAndGetAllFields(vlForm) },
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
      videoDecodeValues,
      recordingValues,
      publicMediaValues,
      messageQueueValues,
      opsValues,
      rotationValues,
      vlValues,
    ] = results.map((result) => (result.status === 'fulfilled' ? result.value : undefined));
    try {
      setSaving(true);
      const [, , , , , inferenceResponse] = await Promise.all([
        updateVlConfig(vlValues),
        updateSourceRotationConfig(rotationValues),
        updateVideoDecodeConfig(videoDecodeValues),
        updateRecordingStorageConfig(recordingValues),
        updateOpsNotificationConfig(opsValues),
        updateInferenceResourceConfig(inferenceValues),
        updatePublicMediaConfig(publicMediaValues),
        updateMessageQueueConfig(messageQueueValues),
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

  const handleTestMessageQueue = async () => {
    try {
      setTestingMq(true);
      const values = await messageQueueForm.validateFields();
      const response = await testMessageQueueConfig(values);
      if (response?.success) {
        message.success(response.message || '消息投递连接正常');
      } else {
        message.error(response?.error || '测试连接失败');
      }
    } catch (error: any) {
      message.error(`测试连接失败: ${error.message || error.error || '未知错误'}`);
    } finally {
      setTestingMq(false);
    }
  };

  const handleCopyHttpPrompt = async () => {
    const copied = await copyToClipboard(httpReceiverPrompt);
    if (copied) {
      message.success('接收端 Prompt 已复制');
    } else {
      message.error('复制失败，请手动选择并复制');
    }
  };

  const handleTestObjectStorage = async () => {
    try {
      const values = await validateAndGetAllFields(publicMediaForm);
      setTestingObjectStorage(true);
      const response = await testObjectStorageConfig(values);
      message.success(response.message || '对象存储连接正常');
    } catch (error: any) {
      if (error?.errorFields) return;
      message.error(`对象存储测试失败: ${error.message || error.error || '未知错误'}`);
    } finally {
      setTestingObjectStorage(false);
    }
  };

  const handleRetryFailedDeliveries = async () => {
    try {
      setRetryingDeliveries(true);
      const response = await retryFailedAlertDeliveries();
      setDeliveryStats(response.delivery_stats);
      message.success(response.message);
    } catch (error: any) {
      message.error(`重新投递失败: ${error.message || error.error || '未知错误'}`);
    } finally {
      setRetryingDeliveries(false);
    }
  };

  return (
    <div className="system-settings-page">
      <PageHeader
        icon={<SettingOutlined />}
        eyebrow="SYSTEM CONTROL"
        title="系统设置"
        subtitle="统一管理推理资源、录像存储、运维通知、视频轮转、API Key、VL 核验与消息投递配置。"
        extra={!['apiKeys', 'license'].includes(activeTabKey) ? (
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
        <>
          <section className="node-identity-strip" aria-label="当前系统节点身份">
            <div className="node-identity-primary">
              <span className="node-identity-icon" aria-hidden="true"><ApartmentOutlined /></span>
              <div>
                <span className="node-identity-eyebrow">当前系统节点</span>
                <Typography.Text
                  className="node-identity-value"
                  copyable={systemInfo?.node_id ? { text: systemInfo.node_id, tooltips: ['复制节点 ID', '已复制'] } : false}
                >
                  {systemInfo?.node_id || '未获取'}
                </Typography.Text>
                <small>用于消息主题、告警来源标识和集群去重</small>
              </div>
            </div>
            <dl className="node-identity-meta">
              <div>
                <dt>身份来源</dt>
                <dd>{NODE_ID_SOURCE_LABELS[systemInfo?.node_id_source || ''] || systemInfo?.node_id_source || '未知'}</dd>
              </div>
              <div>
                <dt>主机名</dt>
                <dd title={systemInfo?.hostname}>{systemInfo?.hostname || '未知'}</dd>
              </div>
            </dl>
          </section>

          <Tabs
            className="system-settings-tabs"
            activeKey={activeTabKey}
            onChange={setActiveTabKey}
            items={[
            {
              key: 'license',
              label: (<span><SafetyCertificateOutlined /> 许可证</span>),
              children: (
                <Card className="system-settings-card" title={<span><SafetyCertificateOutlined /> 许可证与资源额度</span>}>
                  <LicenseSettingsCard />
                </Card>
              ),
            },
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
              key: 'videoDecode',
              label: (<span><VideoCameraOutlined /> 视频解码</span>),
              children: (
                <Card
                  className="system-settings-card"
                  title={<span><VideoCameraOutlined /> 视频解码策略</span>}
                  extra={<span className="inference-config-source">来源：{videoDecodeConfigSource === 'database' ? '系统配置' : '环境变量默认值'}</span>}
                >
                  <Alert
                    type={decodeKeyframesOnly ? 'warning' : 'success'}
                    showIcon
                    className="system-settings-alert"
                    message={decodeKeyframesOnly ? '仅解码关键帧已开启' : '当前解码全部帧'}
                    description={decodeKeyframesOnly
                      ? '仅适合明确接受降低时间分辨率的场景；保存后，继承系统配置的视频源会在 5 秒内自动重启。'
                      : '默认推荐配置。算法始终消费解码队列中的最新帧，减少队列堆积造成的画面延迟。'}
                  />

                  <Form form={videoDecodeForm} layout="vertical">
                    <Form.Item
                      label="仅解码关键帧"
                      name="decode_keyframes_only"
                      valuePropName="checked"
                      extra="单个视频源中明确设置的开关优先于此全局配置；选择“继承系统配置”的视频源使用这里的值。"
                    >
                      <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                    </Form.Item>
                  </Form>
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
              label: (<span><GlobalOutlined /> 告警媒体</span>),
              children: (
                <Card
                  className="system-settings-card"
                  title={<span><GlobalOutlined /> 告警媒体交付</span>}
                  extra={mediaDeliveryMode === 'object_storage' ? (
                    <Button icon={<ApiOutlined />} loading={testingObjectStorage} onClick={handleTestObjectStorage}>
                      测试对象存储
                    </Button>
                  ) : undefined}
                >
                  <Alert
                    type="info"
                    showIcon
                    className="system-settings-alert"
                    message="消息投递使用持久化异步任务"
                    description="URL 模式保持当前行为：录像走 Nginx 公开目录 /api/video/，告警导出 ZIP 走 /media/exports/，不再经过 API 进程，避免大文件 502。消息内嵌模式发送 Base64 标注图；对象存储模式先发文字告警，上传成功后再发送媒体就绪消息。"
                  />

                  <Form form={publicMediaForm} layout="vertical">
                    <div className="system-settings-form-grid">
                      <Form.Item label="媒体交付模式" name="delivery_mode" rules={[{ required: true, message: '请选择媒体交付模式' }]}>
                        <Select options={[
                          { value: 'url', label: '盒子 URL（默认）' },
                          { value: 'inline', label: '消息内嵌图片' },
                          { value: 'object_storage', label: 'S3 兼容对象存储' },
                        ]} />
                      </Form.Item>

                      {mediaDeliveryMode === 'url' ? (
                        <>
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
                          <Form.Item label="链接有效期（小时）" name="media_url_ttl_hours" rules={[{ required: mediaSigningEnabled, message: '请输入链接有效期' }]}>
                            <InputNumber min={1} max={720} precision={0} disabled={!mediaSigningEnabled} style={{ width: '100%' }} />
                          </Form.Item>
                        </>
                      ) : null}

                      {mediaDeliveryMode === 'inline' ? (
                        <>
                          <Form.Item label="最大消息图片（字节）" name={['inline', 'max_bytes']} rules={[{ required: true, message: '请输入大小上限' }]}>
                            <InputNumber min={32768} max={8388608} precision={0} style={{ width: '100%' }} />
                          </Form.Item>
                          <Form.Item label="图片最大边（像素）" name={['inline', 'max_edge']} rules={[{ required: true, message: '请输入最大边' }]}>
                            <InputNumber min={320} max={4096} precision={0} style={{ width: '100%' }} />
                          </Form.Item>
                          <Form.Item label="JPEG 初始质量" name={['inline', 'jpeg_quality']} rules={[{ required: true, message: '请输入图片质量' }]}>
                            <InputNumber min={30} max={95} precision={0} style={{ width: '100%' }} />
                          </Form.Item>
                        </>
                      ) : null}

                      {mediaDeliveryMode === 'object_storage' ? (
                        <>
                          <Form.Item className="system-settings-field-span-2" label="Endpoint" name={['object_storage', 'endpoint_url']} rules={[{ required: true, message: '请输入 Endpoint' }, { type: 'url', message: '请输入有效的 HTTP/HTTPS 地址' }]}>
                            <Input placeholder="https://s3.example.com" />
                          </Form.Item>
                          <Form.Item label="Region" name={['object_storage', 'region']}>
                            <Input placeholder="us-east-1（可选）" />
                          </Form.Item>
                          <Form.Item label="Bucket" name={['object_storage', 'bucket']} rules={[{ required: true, message: '请输入 Bucket' }]}>
                            <Input placeholder="video-alerts" />
                          </Form.Item>
                          <Form.Item label="Access Key ID" name={['object_storage', 'access_key_id']} rules={[{ required: true, message: '请输入 Access Key ID' }]}>
                            <Input autoComplete="off" />
                          </Form.Item>
                          <Form.Item
                            label="Secret Access Key"
                            name={['object_storage', 'secret_access_key']}
                            rules={[{ required: !publicMediaConfig?.object_storage?.secret_configured, message: '请输入 Secret Access Key' }]}
                            extra={publicMediaConfig?.object_storage?.secret_configured ? '已配置；留空保持原值' : '尚未配置'}
                          >
                            <Input.Password autoComplete="new-password" />
                          </Form.Item>
                          <Form.Item label="对象前缀" name={['object_storage', 'key_prefix']}>
                            <Input placeholder="alerts" />
                          </Form.Item>
                          <Form.Item label="预签名有效期（小时）" name={['object_storage', 'presigned_url_ttl_hours']} rules={[{ required: true, message: '请输入有效期' }]}>
                            <InputNumber min={1} max={168} precision={0} style={{ width: '100%' }} />
                          </Form.Item>
                          <Form.Item label="Path-style 寻址" name={['object_storage', 'force_path_style']} valuePropName="checked">
                            <Switch />
                          </Form.Item>
                          <Form.Item label="验证 TLS 证书" name={['object_storage', 'verify_ssl']} valuePropName="checked">
                            <Switch />
                          </Form.Item>
                        </>
                      ) : null}

                      <Form.Item label="最大尝试次数" name={['async_delivery', 'max_attempts']} rules={[{ required: true, message: '请输入最大尝试次数' }]}>
                        <InputNumber min={1} max={100} precision={0} style={{ width: '100%' }} />
                      </Form.Item>
                      <Form.Item label="初始退避（秒）" name={['async_delivery', 'initial_backoff_seconds']} rules={[{ required: true, message: '请输入初始退避' }]}>
                        <InputNumber min={1} max={300} precision={0} style={{ width: '100%' }} />
                      </Form.Item>
                      <Form.Item label="最大退避（秒）" name={['async_delivery', 'max_backoff_seconds']} rules={[{ required: true, message: '请输入最大退避' }]}>
                        <InputNumber min={1} max={86400} precision={0} style={{ width: '100%' }} />
                      </Form.Item>
                    </div>
                  </Form>

                  <div className="delivery-status-row" aria-label="异步投递状态">
                    <Tag>待投递 {deliveryStats.pending}</Tag>
                    <Tag color="processing">处理中 {deliveryStats.processing}</Tag>
                    <Tag color="warning">重试中 {deliveryStats.retrying}</Tag>
                    <Tag color={deliveryStats.failed ? 'error' : 'default'}>失败 {deliveryStats.failed}</Tag>
                    <Button size="small" icon={<SyncOutlined />} loading={retryingDeliveries} disabled={!deliveryStats.failed} onClick={handleRetryFailedDeliveries}>
                      重试失败任务
                    </Button>
                  </div>
                </Card>
              ),
            },
            {
              key: 'messageQueue',
              label: (<span><ApiOutlined /> 消息投递</span>),
              children: (
                <Card
                  className="system-settings-card"
                  title={<span><ApiOutlined /> 消息投递</span>}
                  extra={
                    <Button
                      icon={<ApiOutlined />}
                      loading={testingMq}
                      onClick={handleTestMessageQueue}
                      disabled={!messageQueueEnabled}
                    >
                      {messageQueueProvider === 'http' ? '发送测试事件' : '测试连接'}
                    </Button>
                  }
                >
                  <Alert
                    type={messageQueueEnabled ? 'info' : 'warning'}
                    showIcon
                    className="system-settings-alert"
                    message={messageQueueEnabled ? `${messageQueueProvider.toUpperCase()} 预警投递已启用` : '预警消息投递未启用'}
                    description="系统每次只使用一个通道。MQTT 为默认通道，RabbitMQ 用于兼容现有消费端，HTTP 用于直接调用接收端 API。所有通道均复用持久化异步投递与失败重试。"
                  />

                  <Form form={messageQueueForm} layout="vertical">
                    <div className="system-settings-form-grid">
                      <Form.Item label="启用消息投递" name="enabled" valuePropName="checked" extra="关闭后不再连接或投递。">
                        <Switch />
                      </Form.Item>

                      <Form.Item
                        label="投递通道"
                        name="provider"
                        rules={[{ required: true, message: '请选择提供方' }]}
                      >
                        <Select
                          disabled={!messageQueueEnabled}
                          options={[
                            { value: 'mqtt', label: 'MQTT（推荐）' },
                            { value: 'rabbitmq', label: 'RabbitMQ（兼容）' },
                            { value: 'http', label: 'HTTP API' },
                          ]}
                        />
                      </Form.Item>

                      {messageQueueProvider === 'mqtt' ? (
                        <>
                          <Form.Item label="Broker 主机" name={['mqtt', 'host']} rules={[{ required: messageQueueEnabled, message: '请输入 Broker 主机' }]}>
                            <Input placeholder="mqtt 或 10.0.4.15" disabled={!messageQueueEnabled} />
                          </Form.Item>
                          <Form.Item label="端口" name={['mqtt', 'port']} rules={[{ required: messageQueueEnabled, message: '请输入端口' }]}>
                            <InputNumber min={1} max={65535} precision={0} disabled={!messageQueueEnabled} style={{ width: '100%' }} />
                          </Form.Item>
                          <Form.Item label="用户名" name={['mqtt', 'username']} extra="外部 Broker 允许匿名时可以留空。">
                            <Input placeholder="video-ba" disabled={!messageQueueEnabled} />
                          </Form.Item>
                          <Form.Item label="密码" name={['mqtt', 'password']} extra="留空则保留已保存的密码。内置 Broker 首次部署可执行 docker compose exec mqtt cat /mosquitto/secrets/initial-password 获取随机密码。">
                            <Input.Password placeholder="请输入密码" disabled={!messageQueueEnabled} autoComplete="new-password" />
                          </Form.Item>
                          <Form.Item label="主题前缀" name={['mqtt', 'topic_prefix']} rules={[{ required: messageQueueEnabled, message: '请输入主题前缀' }]} extra="实际主题：{前缀}/{node_id}/{alert_type}；不允许 + 和 #。">
                            <Input placeholder="video/alert" disabled={!messageQueueEnabled} />
                          </Form.Item>
                          <Form.Item label="连接超时（秒）" name={['mqtt', 'connection_timeout_seconds']} rules={[{ required: messageQueueEnabled, message: '请输入连接超时' }]}>
                            <InputNumber min={1} max={300} precision={0} disabled={!messageQueueEnabled} style={{ width: '100%' }} />
                          </Form.Item>
                          <Form.Item label="PUBACK 超时（秒）" name={['mqtt', 'publish_timeout_seconds']} rules={[{ required: messageQueueEnabled, message: '请输入确认超时' }]}>
                            <InputNumber min={1} max={300} precision={0} disabled={!messageQueueEnabled} style={{ width: '100%' }} />
                          </Form.Item>
                          <Form.Item label="Keep Alive（秒）" name={['mqtt', 'keepalive_seconds']} rules={[{ required: messageQueueEnabled, message: '请输入 Keep Alive' }]}>
                            <InputNumber min={5} max={3600} precision={0} disabled={!messageQueueEnabled} style={{ width: '100%' }} />
                          </Form.Item>
                        </>
                      ) : messageQueueProvider === 'rabbitmq' ? (
                        <>
                          <Form.Item label="主机地址" name={['rabbitmq', 'host']} rules={[{ required: messageQueueEnabled, message: '请输入主机地址' }]}>
                            <Input placeholder="rabbitmq 或 10.0.4.15" disabled={!messageQueueEnabled} />
                          </Form.Item>
                          <Form.Item label="端口" name={['rabbitmq', 'port']} rules={[{ required: messageQueueEnabled, message: '请输入端口' }]}>
                            <InputNumber min={1} max={65535} precision={0} disabled={!messageQueueEnabled} style={{ width: '100%' }} />
                          </Form.Item>
                          <Form.Item label="虚拟主机" name={['rabbitmq', 'vhost']} rules={[{ required: messageQueueEnabled, message: '请输入虚拟主机' }]}>
                            <Input placeholder="/" disabled={!messageQueueEnabled} />
                          </Form.Item>
                          <Form.Item label="用户名" name={['rabbitmq', 'username']} rules={[{ required: messageQueueEnabled, message: '请输入用户名' }]}>
                            <Input placeholder="admin" disabled={!messageQueueEnabled} />
                          </Form.Item>
                          <Form.Item label="密码" name={['rabbitmq', 'password']} extra="留空则保留已保存的密码。">
                            <Input.Password placeholder="请输入密码" disabled={!messageQueueEnabled} autoComplete="new-password" />
                          </Form.Item>
                          <Form.Item label="交换机名称" name={['rabbitmq', 'alert_exchange']} rules={[{ required: messageQueueEnabled, message: '请输入交换机名称' }]}>
                            <Input placeholder="video_alerts" disabled={!messageQueueEnabled} />
                          </Form.Item>
                          <Form.Item label="交换机类型" name={['rabbitmq', 'exchange_type']} rules={[{ required: messageQueueEnabled, message: '请选择交换机类型' }]}>
                            <Select disabled={!messageQueueEnabled} options={[{ value: 'topic', label: 'topic（按节点/类型订阅）' }, { value: 'direct', label: 'direct（固定 routing key）' }]} />
                          </Form.Item>
                          <Form.Item label="Routing Key" name={['rabbitmq', 'alert_routing_key']} extra="仅 direct 模式使用。">
                            <Input placeholder="alert" disabled={!messageQueueEnabled} />
                          </Form.Item>
                          <Form.Item label="队列名" name={['rabbitmq', 'alert_queue']} extra="生产者不声明队列，仅作消费端约定。">
                            <Input placeholder="video_alerts" disabled={!messageQueueEnabled} />
                          </Form.Item>
                          <Form.Item label="连接超时（秒）" name={['rabbitmq', 'connection_timeout_seconds']} rules={[{ required: messageQueueEnabled, message: '请输入连接超时' }]}>
                            <InputNumber min={1} max={300} precision={0} disabled={!messageQueueEnabled} style={{ width: '100%' }} />
                          </Form.Item>
                        </>
                      ) : (
                        <>
                          <Form.Item
                            className="system-settings-field-span-2"
                            label="接收端 URL"
                            name={['http', 'endpoint_url']}
                            rules={[
                              { required: messageQueueEnabled, message: '请输入接收端 URL' },
                              { type: 'url', message: '请输入有效的 HTTP/HTTPS 地址' },
                            ]}
                            extra="告警和测试事件都会以 application/json POST 到该地址；不会跟随重定向。"
                          >
                            <Input placeholder="https://receiver.example.com/api/v1/video-ba/events" disabled={!messageQueueEnabled} />
                          </Form.Item>

                          <Form.Item label="请求超时（秒）" name={['http', 'timeout_seconds']} rules={[{ required: messageQueueEnabled, message: '请输入请求超时' }]}>
                            <InputNumber min={1} max={300} precision={0} disabled={!messageQueueEnabled} style={{ width: '100%' }} />
                          </Form.Item>

                          <Form.Item
                            label="HMAC-SHA256 共享密钥"
                            name={['http', 'hmac_secret']}
                            rules={[
                              {
                                required: messageQueueEnabled && !messageQueueForm.getFieldValue(['http', 'hmac_secret_configured']),
                                message: '请输入 HMAC 共享密钥',
                              },
                              { min: 16, message: '共享密钥至少需要 16 个字符' },
                            ]}
                            extra="HTTP 投递固定使用 HMAC-SHA256。发送端和接收端配置相同密钥；密钥不会随请求发送，留空会保留已保存的值。生产环境建议使用至少 32 位随机字符串。"
                          >
                            <Input.Password placeholder="请输入至少 16 个字符的共享密钥" disabled={!messageQueueEnabled} autoComplete="new-password" />
                          </Form.Item>

                          <Form.List name={['http', 'custom_headers']}>
                            {(fields, { add, remove }) => (
                              <div className="http-header-editor system-settings-field-span-2">
                                <div className="http-header-editor__heading">
                                  <div>
                                    <strong>自定义请求头</strong>
                                    <span>请求头值保存后全部脱敏；留空保留原值。</span>
                                  </div>
                                  <Button
                                    size="small"
                                    icon={<PlusOutlined />}
                                    onClick={() => add({ name: '', value: '' })}
                                    disabled={!messageQueueEnabled}
                                  >
                                    添加请求头
                                  </Button>
                                </div>
                                {fields.length === 0 ? (
                                  <div className="http-header-editor__empty">没有额外请求头</div>
                                ) : fields.map((field) => (
                                  <div className="http-header-editor__row" key={field.key}>
                                    <Form.Item
                                      {...field}
                                      key={`${field.key}-name`}
                                      name={[field.name, 'name']}
                                      rules={[{ required: true, message: '请输入请求头名称' }]}
                                    >
                                      <Input placeholder="X-API-Key" disabled={!messageQueueEnabled} />
                                    </Form.Item>
                                    <Form.Item {...field} key={`${field.key}-value`} name={[field.name, 'value']}>
                                      <Input.Password placeholder="留空保留已保存值" disabled={!messageQueueEnabled} autoComplete="new-password" />
                                    </Form.Item>
                                    <Button
                                      type="text"
                                      danger
                                      aria-label="删除请求头"
                                      icon={<DeleteOutlined />}
                                      onClick={() => remove(field.name)}
                                      disabled={!messageQueueEnabled}
                                    />
                                  </div>
                                ))}
                              </div>
                            )}
                          </Form.List>

                          <div className="http-prompt-card system-settings-field-span-2">
                            <div className="http-prompt-card__header">
                              <div>
                                <span className="http-prompt-card__eyebrow">RECEIVER CONTRACT</span>
                                <strong>Vibe Coding 接收端 Prompt</strong>
                              </div>
                              <Button size="small" icon={<CopyOutlined />} onClick={handleCopyHttpPrompt}>
                                复制 Prompt
                              </Button>
                            </div>
                            <Typography.Paragraph className="http-prompt-card__hint">
                              已根据当前 URL、鉴权、请求头和媒体模式生成，并包含 URL、Base64 内嵌、对象存储三种接收分支。HMAC Prompt 会说明签名原文、时钟窗口与防重放校验；所有密钥始终使用占位符。
                            </Typography.Paragraph>
                            <pre className="http-prompt-card__content">{httpReceiverPrompt}</pre>
                          </div>
                        </>
                      )}
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
        </>
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
