import React, { useState, useEffect, useRef } from 'react';
import { useParams } from 'umi';
import { Form, Input, Select, Tabs, Space, Tag, Switch, InputNumber, Typography, List, message } from 'antd';
import Button from '@/components/common/AppButton';
import AppEmptyState from '@/components/common/AppEmptyState';
import DetectionResponseContract from '@/components/external-api/DetectionResponseContract';
import {
  SettingOutlined,
  DeleteOutlined,
  InfoCircleOutlined,
  SearchOutlined,
  VideoCameraOutlined,
  EditOutlined,
  CheckCircleOutlined,
  PlusOutlined,
  MinusCircleOutlined,
  ExperimentOutlined,
} from '@ant-design/icons';
import { getNodeTypes } from './nodes';
import VideoSourceSelector from './VideoSourceSelector';
import ROIDrawer, { getROIAnchorLabel, ROIRegion } from './ROIDrawer';
import TimeScheduleEditor from './TimeScheduleEditor';
import HttpConditionBuilder from './HttpConditionBuilder';
import { testHttpRequest } from '@/services/api';
import {
  createDefaultWeeklySchedule,
  normalizeWeeklySchedule,
  WEEKDAYS,
  WeeklySchedule,
} from '../utils/timeSchedule';
import {
  getTrackerEventFormValues,
  isTrackerAlgorithm,
  trackerEventConfigFromForm,
} from '../utils/algorithmDefaults';
import './PropertyPanel.css';

const { TextArea } = Input;
const { Option } = Select;
const { Text } = Typography;

const WEBHOOK_CREDENTIAL_FORM_FIELDS = new Set([
  'webhookEndpointUrl',
  'webhookSigningSecret',
  'webhookBarkDeviceKey',
]);

type WebhookCredentialPatch = Partial<Record<
  'endpoint_url' | 'signing_secret' | 'device_key',
  string
>>;

const buildWebhookConfig = (
  values: Record<string, any>,
  currentConfig: Record<string, any>,
  credentialPatch?: WebhookCredentialPatch,
) => {
  const config = { ...currentConfig };
  const providerOptions = { ...(config.provider_options || {}) };

  config.provider = values.webhookProvider || 'generic';
  config.title_template = values.webhookTitleTemplate || '【{{alert.level}}】{{alert.type}}';
  config.body_template = values.webhookBodyTemplate || '{{alert.message}}';
  config.include_media_urls = values.webhookIncludeMediaUrls !== false;
  config.public_base_url = (values.webhookPublicBaseUrl || '').trim();
  config.timeout_seconds = values.webhookTimeoutSeconds || 5;
  config.max_attempts = values.webhookMaxAttempts || 3;
  config.retry_backoff_seconds = values.webhookRetryBackoffSeconds || 1;
  config.headers = values.webhookHeaders ? JSON.parse(values.webhookHeaders) : [];
  config.payload_template = values.webhookPayloadTemplate ? JSON.parse(values.webhookPayloadTemplate) : {};

  providerOptions.group = values.webhookBarkGroup || 'VideoBA';
  providerOptions.sound = values.webhookBarkSound || '';
  providerOptions.level = values.webhookBarkLevel || 'active';

  if (credentialPatch?.endpoint_url) config.endpoint_url = credentialPatch.endpoint_url;
  if (credentialPatch?.signing_secret) providerOptions.signing_secret = credentialPatch.signing_secret;
  if (credentialPatch?.device_key) providerOptions.device_key = credentialPatch.device_key;

  config.provider_options = providerOptions;
  return config;
};

const buildHttpRequestConfig = (
  values: Record<string, any>,
  currentConfig: Record<string, any>,
) => ({
  ...currentConfig,
  method: values.httpMethod || 'GET',
  url: String(values.httpUrl || '').trim(),
  query_params: values.httpQueryParams || [],
  headers: values.httpHeaders || [],
  json_body: values.httpMethod === 'GET'
    ? null
    : JSON.parse(values.httpJsonBody || 'null'),
  timeout_seconds: values.httpTimeoutSeconds ?? 30,
  interval_seconds: values.httpIntervalSeconds ?? 1,
  extractors: values.httpExtractors || [],
});

export interface PropertyPanelProps {
  node: any;
  videoSources: any[];
  externalApis?: any[];
  algorithms?: any[];
  vlConfig?: any;
  edges?: any[];
  nodes?: any[];
  isTemplate?: boolean;
  onUpdate: (nodeId: string, data: any) => void;
  onDelete: (nodeId: string) => void;
}

export interface EdgePropertyPanelProps {
  edge: any;
  nodes?: any[];
  onUpdate: (edgeId: string, data: { condition: 'detected' | 'not_detected' | null }) => void;
  onDelete: (edgeId: string) => void;
}

function persistNonConditionEdgeCondition(raw: unknown): 'detected' | 'not_detected' | null {
  if (raw === 'detected' || raw === 'not_detected') {
    return raw;
  }
  return null;
}

export const EdgePropertyPanel: React.FC<EdgePropertyPanelProps> = ({
  edge,
  nodes = [],
  onUpdate,
  onDelete,
}) => {
  const [form] = Form.useForm();
  const sourceNode = nodes.find((node) => node.id === edge?.source);
  const isConditionSource = sourceNode?.type === 'condition' || sourceNode?.data?.type === 'condition';
  const sourceHandle = edge?.sourceHandle;
  const portLabel = sourceHandle === 'no' || sourceHandle === 'false'
    ? '否（false）'
    : '是（true）';

  useEffect(() => {
    if (!edge) return;
    const stored = edge.data?.condition;
    form.setFieldsValue({
      condition: stored === 'detected' || stored === 'not_detected' ? stored : 'always',
    });
  }, [edge?.id, edge?.data?.condition, form]);

  return (
    <div className="property-panel">
      <div className="panel-header">
        <Space size="small">
          <SettingOutlined />
          <span className="panel-title">连线属性</span>
        </Space>
        <Button
          type="text"
          size="small"
          icon={<DeleteOutlined />}
          onClick={() => onDelete(edge.id)}
          className="delete-btn"
        >
          删除
        </Button>
      </div>

      <div className="property-tabs" style={{ padding: '20px 24px' }}>
        {isConditionSource ? (
          <div className="info-box" style={{ background: '#f0f5ff', borderColor: '#adc6ff', color: '#1d39c4' }}>
            <InfoCircleOutlined />
            <span>
              条件节点连线由「是 / 否」端口决定（当前端口：{portLabel}），不能在此覆盖。
            </span>
          </div>
        ) : (
          <Form
            form={form}
            layout="vertical"
            size="small"
            onValuesChange={(_, values) => {
              onUpdate(edge.id, { condition: persistNonConditionEdgeCondition(values.condition) });
            }}
          >
            <Form.Item
              label="触发条件"
              name="condition"
              extra="「总是」只存在于编辑器，保存为 JSON null"
            >
              <Select>
                <Option value="always">总是</Option>
                <Option value="detected">检测到</Option>
                <Option value="not_detected">未检测到</Option>
              </Select>
            </Form.Item>
          </Form>
        )}
      </div>
    </div>
  );
};

const PropertyPanel: React.FC<PropertyPanelProps> = ({
  node,
  videoSources,
  externalApis = [],
  algorithms = [],
  vlConfig,
  edges = [],
  nodes = [],
  isTemplate = false,
  onUpdate,
  onDelete,
}) => {
  const { id: workflowIdParam } = useParams<{ id?: string }>();
  const [form] = Form.useForm();
  const [activeTab, setActiveTab] = useState('basic');
  const [selectorVisible, setSelectorVisible] = useState(false);
  const [roiDrawerVisible, setRoiDrawerVisible] = useState(false);
  const [httpTesting, setHttpTesting] = useState(false);
  const [httpTestResult, setHttpTestResult] = useState<any>(null);
  const vlConfigReady = Boolean(vlConfig?.has_required_fields);
  const selectedAlgorithm = algorithms.find((item) => String(item.id) === String(node?.data?.dataId ?? node?.data?.algorithmId));
  const selectedAlgorithmType = node?.data?.algorithmType || selectedAlgorithm?.algorithm_type || 'script';
  const isVlAlgorithm = selectedAlgorithmType === 'vl';
  const isOcrAlgorithm = selectedAlgorithmType === 'ocr';
  const isTrackerEventAlgorithm = isTrackerAlgorithm(selectedAlgorithm);
  const vlDefaultTimeout = Number(selectedAlgorithm?.vl_config?.timeout_seconds || 30);

  // 只允许最后一次表单校验结果写入节点，避免快速输入时旧值覆盖新值。
  const updateVersionRef = useRef(0);
  const autoUpdateTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const httpTestVersionRef = useRef(0);
  const activeNodeIdRef = useRef(node?.id);
  activeNodeIdRef.current = node?.id;

  useEffect(() => {
    httpTestVersionRef.current += 1;
    setHttpTesting(false);
    setHttpTestResult(null);
  }, [node?.id]);

  console.log('Available videoSources:', videoSources);
  console.log('onUpdate 函数:', onUpdate);
  console.log('onUpdate 函数名:', onUpdate.name);

  // 当 node 变化时，回显节点数据到表单
  useEffect(() => {
    if (node) {
      const nodeConfig = node.data?.config || {};
      const nodeType = node.data?.type || node.type;

      console.log('🔄 PropertyPanel useEffect 触发');
      console.log('📦 节点类型:', nodeType);
      console.log('🎥 videoSourceId:', node.data.videoSourceId, 'videoSourceName:', node.data.videoSourceName);
      // 获取当前表单值，检查表单是否已经有值
      const currentFormValues = form.getFieldsValue();

      // 对于视频源节点，如果表单中已经有 videoSourceId，且与 node.data 中的一致，说明是同一次渲染，不需要重新初始化
      if ((nodeType === 'videoSource' || nodeType === 'source') && currentFormValues.videoSourceId !== undefined) {
        if (currentFormValues.videoSourceId == node.data.videoSourceId) {
          console.log('⏸️ 表单值与节点数据一致，跳过重复初始化');
          return;
        }
      }

      // 根据节点类型设置不同的表单值
      const formValues: any = {
        label: node.data.label,
        description: node.data.description || '',
      };

      // 根据节点类型回显特定字段
      if (nodeType === 'videoSource' || nodeType === 'source') {
        // 确保 videoSourceId 的类型与 videoSources 中的 id 类型一致
        const sourceId = node.data.dataId ?? node.data.videoSourceId;
        if (sourceId !== undefined && sourceId !== null) {
          // 找到匹配的视频源来确认类型
          const matchingSource = videoSources.find(s => String(s.id) === String(sourceId));
          if (matchingSource) {
            // 使用匹配到的源的id，确保类型一致
            formValues.videoSourceId = matchingSource.id;
            console.log('视频源匹配成功:', {
              nodeSourceId: sourceId,
              nodeSourceType: typeof sourceId,
              matchedSourceId: matchingSource.id,
              matchedSourceType: typeof matchingSource.id,
              matchedSourceName: matchingSource.name
            });
          } else {
            console.warn('未找到匹配的视频源:', sourceId, '可用视频源:', videoSources);
            formValues.videoSourceId = sourceId;
          }
        }
      } else if (nodeType === 'algorithm') {
        formValues.confidence = nodeConfig.confidence ?? node.data.confidence ?? node.data.defaultConfidence ?? 0.5;

        // 执行配置
        formValues.intervalSeconds = nodeConfig.interval_seconds || 1;
        formValues.vlTimeoutOverrideEnabled = isVlAlgorithm && nodeConfig.runtime_timeout_override_enabled === true;
        formValues.runtimeTimeout = formValues.vlTimeoutOverrideEnabled
          ? (nodeConfig.runtime_timeout || vlDefaultTimeout)
          : (isVlAlgorithm ? vlDefaultTimeout : (nodeConfig.runtime_timeout || 30));
        formValues.memoryLimitMb = nodeConfig.memory_limit_mb || 512;
        formValues.labelName = nodeConfig.label_name || (isVlAlgorithm ? 'VL Result' : isOcrAlgorithm ? 'OCR Text' : 'Object');
        formValues.labelColor = nodeConfig.label_color || (isVlAlgorithm ? '#13c2c2' : isOcrAlgorithm ? '#1677ff' : '#FF0000');
        if (isOcrAlgorithm) {
          formValues.inputMode = nodeConfig.input_mode || 'frame';
          formValues.expandRatio = nodeConfig.expand_ratio ?? 0.1;
          formValues.maxCandidates = nodeConfig.max_candidates ?? 8;
          formValues.upstreamClassFilter = nodeConfig.upstream_class_filter || [];
        }
        if (isTrackerEventAlgorithm) {
          Object.assign(formValues, getTrackerEventFormValues(selectedAlgorithm, nodeConfig));
        }
      } else if (nodeType === 'externalApi' || nodeType === 'external_api') {
        formValues.externalApiId = node.data.dataId ?? node.data.externalApiId;
        formValues.executionMode = nodeConfig.execution_mode || node.data.executionMode || 'sync';
        formValues.intervalSeconds = nodeConfig.interval_seconds || 1;
        formValues.timeoutSeconds = nodeConfig.timeout_seconds || 30;
        formValues.includeImage = nodeConfig.include_image !== false;
        formValues.includeUpstreamResults = nodeConfig.include_upstream_results !== false;
        formValues.payloadTemplate = JSON.stringify(nodeConfig.payload_template || {}, null, 2);
        formValues.outputMapping = JSON.stringify(
          nodeConfig.output_mapping || {
            has_detection_path: 'has_detection',
            detections_path: 'detections',
            metadata_path: 'metadata',
          },
          null,
          2,
        );
      } else if (nodeType === 'httpRequest' || nodeType === 'http_request') {
        formValues.httpMethod = nodeConfig.method || 'GET';
        formValues.httpUrl = nodeConfig.url || '';
        formValues.httpQueryParams = nodeConfig.query_params || [];
        formValues.httpHeaders = nodeConfig.headers || [];
        formValues.httpJsonBody = JSON.stringify(nodeConfig.json_body ?? null, null, 2);
        formValues.httpTimeoutSeconds = nodeConfig.timeout_seconds ?? 30;
        formValues.httpIntervalSeconds = nodeConfig.interval_seconds ?? 1;
        formValues.httpExtractors = nodeConfig.extractors || [];
        formValues.httpTestContext = JSON.stringify({
          frame: { timestamp: Date.now() / 1000 },
          nodes: {},
        }, null, 2);
      } else if (nodeType === 'webhook') {
        const providerOptions = nodeConfig.provider_options || {};
        formValues.webhookProvider = nodeConfig.provider || 'generic';
        formValues.webhookEndpointUrl = nodeConfig.endpoint_url || '';
        formValues.webhookTitleTemplate = nodeConfig.title_template || '【{{alert.level}}】{{alert.type}}';
        formValues.webhookBodyTemplate = nodeConfig.body_template || '{{alert.message}}';
        formValues.webhookIncludeMediaUrls = nodeConfig.include_media_urls !== false;
        formValues.webhookPublicBaseUrl = nodeConfig.public_base_url || '';
        formValues.webhookTimeoutSeconds = nodeConfig.timeout_seconds || 5;
        formValues.webhookMaxAttempts = nodeConfig.max_attempts || 3;
        formValues.webhookRetryBackoffSeconds = nodeConfig.retry_backoff_seconds || 1;
        formValues.webhookHeaders = JSON.stringify(nodeConfig.headers || [], null, 2);
        formValues.webhookPayloadTemplate = JSON.stringify(nodeConfig.payload_template || {}, null, 2);
        formValues.webhookSigningSecret = '';
        formValues.webhookBarkDeviceKey = '';
        formValues.webhookBarkGroup = providerOptions.group || 'VideoBA';
        formValues.webhookBarkSound = providerOptions.sound || '';
        formValues.webhookBarkLevel = providerOptions.level || 'active';
      } else if (nodeType === 'function') {
        // 从 config 中读取函数配置
        const config = { ...(node.data?.config || {}) };
        formValues.functionName = config.function_name || 'area_ratio';
        formValues.threshold = config.threshold ?? 0.7;
        formValues.operator = config.operator || 'less_than';
        formValues.dimension = config.dimension || 'height';
        if (config.input_a?.class_filter) {
          formValues.classFilterA = config.input_a.class_filter.join(',');
        }
        if (config.input_b?.class_filter) {
          formValues.classFilterB = config.input_b.class_filter.join(',');
        }
      } else if (nodeType === 'detectionFilter' || nodeType === 'detection_filter') {
        formValues.filterDimension = nodeConfig.dimension || 'height';
        formValues.filterUnit = nodeConfig.unit || 'pixel';
        formValues.filterComparison = nodeConfig.comparison || 'gte';
        formValues.filterThreshold = nodeConfig.unit === 'ratio'
          ? Number(nodeConfig.threshold ?? 0.05) * 100
          : Number(nodeConfig.threshold ?? 40);
      } else if (nodeType === 'condition') {
        formValues.conditionKind = node.data.conditionKind || node.data.condition_kind || 'count';
        formValues.targetCount = node.data.targetCount || node.data.target_count || 1;
        formValues.comparisonType = node.data.comparisonType || node.data.comparison_type || '>=';
        formValues.sourceNodeId = node.data.sourceNodeId || node.data.source_node_id;
        formValues.textOperator = node.data.textOperator || node.data.text_operator || 'contains';
        formValues.patternType = node.data.patternType || node.data.pattern_type || 'keywords';
        formValues.keywords = node.data.keywords || [];
        formValues.keywordLogic = node.data.keywordLogic || node.data.keyword_logic || 'any';
        formValues.regexPattern = node.data.regexPattern || node.data.regex_pattern || '';
        formValues.caseSensitive = node.data.caseSensitive ?? node.data.case_sensitive ?? false;
        formValues.labels = node.data.labels || [];
        formValues.windowSize = node.data.windowSize ?? node.data.window_size ?? 10;
        formValues.direction = node.data.direction || 'both';
        formValues.relativeThresholdPercent =
          (node.data.relativeThreshold ?? node.data.relative_threshold ?? 0.5) * 100;
        formValues.absoluteThreshold = node.data.absoluteThreshold ?? node.data.absolute_threshold ?? 3;
        formValues.confirmationCount = node.data.confirmationCount ?? node.data.confirmation_count ?? 1;
        formValues.httpExpression = node.data.expression || {
          logic: 'and',
          children: [{ variable: '$success', operator: 'eq', value: true }],
        };
      } else if (nodeType === 'timeSchedule' || nodeType === 'time_schedule') {
        formValues.weeklySchedule = node.data.weeklySchedule
          ? normalizeWeeklySchedule(node.data.weeklySchedule)
          : createDefaultWeeklySchedule();
      } else if (nodeType === 'roi') {
        formValues.roiMode = node.data.roiMode || 'postFilter';
      } else if (nodeType === 'alert') {
        console.log('🔍 Alert节点回显 - node.data:', node.data);
        console.log('🔍 messageFormat 值:', node.data.messageFormat);
        formValues.alertLevel = node.data.alertLevel || 'info';
        formValues.alertMessage = node.data.alertMessage || '检测到目标';
        formValues.alertType = node.data.alertType || 'detection';
        formValues.messageFormat = node.data.messageFormat || 'detailed';
        console.log('✅ formValues.messageFormat 设置为:', formValues.messageFormat);

        // 读取触发条件配置
        const triggerCondition = node.data.triggerCondition;
        if (triggerCondition) {
          formValues.triggerConditionEnable = triggerCondition.enable !== undefined ? triggerCondition.enable : true;
          formValues.triggerConditionWindowSize = triggerCondition.window_size || 30;
          formValues.triggerConditionMode = triggerCondition.mode || 'ratio';
          formValues.triggerConditionThreshold = triggerCondition.threshold !== undefined
            ? triggerCondition.threshold
            : 0.3;
        } else {
          // 默认禁用触发条件
          formValues.triggerConditionEnable = false;
          formValues.triggerConditionWindowSize = 30;
          formValues.triggerConditionMode = 'ratio';
          formValues.triggerConditionThreshold = 0.3;
        }

        // 读取抑制配置
        const suppression = node.data.suppression;
        if (suppression) {
          formValues.suppressionEnable = suppression.enable !== undefined ? suppression.enable : true;
          formValues.suppressionSeconds = suppression.seconds || 60;
        } else {
          // 默认禁用抑制
          formValues.suppressionEnable = false;
          formValues.suppressionSeconds = 60;
        }

        // 读取 MQ 输出开关（默认开启）
        formValues.publishToMq = node.data.publishToMq !== false;

        const vlValidation = node.data.vlValidation;
        formValues.vlValidationEnable = vlConfigReady && vlValidation?.enable === true;
        formValues.vlValidationPromptTemplate = vlValidation?.promptTemplate || vlValidation?.prompt_template || '';
      } else if (nodeType === 'record') {
        formValues.recordDuration = node.data.recordDuration || 10;
      }

      console.log('📝 [PropertyPanel] 即将设置表单值，messageFormat:', formValues.messageFormat);

      // 先重置表单，清除旧值
      form.resetFields();

      // 然后设置新值
      form.setFieldsValue(formValues);

      console.log('✅ 表单初始化完成');

      // 验证表单值是否正确设置
      setTimeout(() => {
        const currentValues = form.getFieldsValue();
        console.log('🔍 [PropertyPanel] 验证表单值，messageFormat:', currentValues.messageFormat);
      }, 100);
    }
  }, [node?.id, form, vlConfigReady, isVlAlgorithm, isOcrAlgorithm, isTrackerEventAlgorithm, vlDefaultTimeout, selectedAlgorithm?.id, selectedAlgorithm?.script_path]);

  useEffect(() => () => {
    if (autoUpdateTimerRef.current) {
      clearTimeout(autoUpdateTimerRef.current);
    }
  }, [node?.id]);

  if (!node) {
    return (
      <div className="property-panel-empty">
        <AppEmptyState
          compact
          title="点击节点或连线查看属性"
          description="点击画布中的节点或连线以编辑其属性"
        />
      </div>
    );
  }

  const handleUpdate = async (version: number) => {
    try {
      const values = await form.validateFields();


      // 处理算法节点的窗口检测配置
      const updatedData: any = { ...values };

      // 特殊处理视频源节点：添加视频源名称和编码
      const nodeType = node.data?.type || node.type;
      if ((nodeType === 'videoSource' || nodeType === 'source') && values.videoSourceId) {
        const selectedSource = videoSources.find(s => String(s.id) === String(values.videoSourceId));
        if (selectedSource) {
          // 重要：也要更新 dataId，否则会被旧数据覆盖
          updatedData.dataId = selectedSource.id;
          updatedData.videoSourceName = selectedSource.name;
          updatedData.videoSourceCode = selectedSource.source_code;
          console.log('✅ 视频源节点更新:', {
            id: selectedSource.id,
            name: selectedSource.name,
            source_code: selectedSource.source_code
          });
        } else {
          console.warn('⚠️ 未找到选中的视频源, videoSourceId:', values.videoSourceId);
        }
      }

      if (nodeType === 'algorithm') {
        const config = { ...(node.data?.config || {}) };
        const confidenceTouched = form.isFieldTouched('confidence');

        if (confidenceTouched) {
          config.confidence = values.confidence ?? node.data.defaultConfidence ?? 0.5;
          config.confidence_override_enabled = true;
        }
        // 保存执行配置
        config.interval_seconds = values.intervalSeconds;
        if (isVlAlgorithm) {
          config.runtime_timeout_override_enabled = values.vlTimeoutOverrideEnabled === true;
          if (config.runtime_timeout_override_enabled) {
            config.runtime_timeout = values.runtimeTimeout;
          } else {
            delete config.runtime_timeout;
          }
          delete config.memory_limit_mb;
        } else if (!isOcrAlgorithm) {
          config.runtime_timeout = values.runtimeTimeout;
          config.memory_limit_mb = values.memoryLimitMb;
        } else {
          delete config.runtime_timeout;
          delete config.memory_limit_mb;
        }
        config.label_name = values.labelName;
        config.label_color = values.labelColor;
        if (isOcrAlgorithm) {
          config.input_mode = values.inputMode || 'frame';
          config.expand_ratio = values.expandRatio ?? 0.1;
          config.max_candidates = values.maxCandidates ?? 8;
          config.upstream_class_filter = values.upstreamClassFilter || [];
        }
        if (isTrackerEventAlgorithm) {
          Object.assign(config, trackerEventConfigFromForm(values));
        }

        updatedData.algorithmType = selectedAlgorithmType;
        updatedData.confidence = config.confidence ?? node.data.defaultConfidence;
        updatedData.config = config;

        delete updatedData.intervalSeconds;
        delete updatedData.runtimeTimeout;
        delete updatedData.memoryLimitMb;
        delete updatedData.labelName;
        delete updatedData.labelColor;
        delete updatedData.inputMode;
        delete updatedData.expandRatio;
        delete updatedData.maxCandidates;
        delete updatedData.upstreamClassFilter;
        delete updatedData.trackEvent;
        delete updatedData.minDwellSeconds;
        delete updatedData.minDisplacePx;
        delete updatedData.maxDisplacePx;
        delete updatedData.disappearSeconds;
        delete updatedData.crossMode;
        delete updatedData.crossDirection;
      } else if (nodeType === 'externalApi' || nodeType === 'external_api') {
        const selectedApi = externalApis.find(item => String(item.id) === String(values.externalApiId));
        const config = { ...(node.data?.config || {}) };

        config.execution_mode = values.executionMode || 'sync';
        config.interval_seconds = values.intervalSeconds;
        config.timeout_seconds = values.timeoutSeconds;
        config.include_image = values.includeImage !== false;
        config.include_upstream_results = values.includeUpstreamResults !== false;
        config.payload_template = values.payloadTemplate ? JSON.parse(values.payloadTemplate) : {};
        config.output_mapping = values.outputMapping ? JSON.parse(values.outputMapping) : {};

        updatedData.dataId = values.externalApiId || null;
        updatedData.externalApiName = selectedApi?.name || node.data?.externalApiName;
        updatedData.executionMode = config.execution_mode;
        updatedData.config = config;

        delete updatedData.externalApiId;
        delete updatedData.executionMode;
        delete updatedData.intervalSeconds;
        delete updatedData.timeoutSeconds;
        delete updatedData.includeImage;
        delete updatedData.includeUpstreamResults;
        delete updatedData.payloadTemplate;
        delete updatedData.outputMapping;
      } else if (nodeType === 'httpRequest' || nodeType === 'http_request') {
        updatedData.config = buildHttpRequestConfig(values, node.data?.config || {});
        Object.keys(updatedData)
          .filter((key) => key.startsWith('http'))
          .forEach((key) => delete updatedData[key]);
      } else if (nodeType === 'webhook') {
        updatedData.config = buildWebhookConfig(values, node.data?.config || {});

        Object.keys(updatedData)
          .filter((key) => key.startsWith('webhook'))
          .forEach((key) => delete updatedData[key]);
      } else if (nodeType === 'alert') {
        // Alert 节点：保存触发条件和抑制配置

        if (vlConfigReady && values.vlValidationEnable === true) {
          const promptTemplate = String(values.vlValidationPromptTemplate || '').trim();
          if (!promptTemplate) {
            form.setFields([{
              name: 'vlValidationPromptTemplate',
              errors: ['启用 VL 核验时必须配置核验提示词模板'],
            }]);
            return;
          }
        }

        // 保存消息格式
        console.log('📝 Alert节点保存 messageFormat:', values.messageFormat);
        updatedData.messageFormat = values.messageFormat || 'detailed';
        console.log('✅ updatedData.messageFormat 已设置为:', updatedData.messageFormat);

        // 保存触发条件配置
        if (values.triggerConditionEnable) {
          updatedData.triggerCondition = {
            enable: true,
            window_size: values.triggerConditionWindowSize || 30,
            mode: values.triggerConditionMode || 'ratio',
            threshold: values.triggerConditionThreshold !== undefined
              ? values.triggerConditionThreshold
              : 0.3,
          };
        } else {
          updatedData.triggerCondition = {
            enable: false,
          };
        }

        // 保存抑制配置
        if (values.suppressionEnable) {
          updatedData.suppression = {
            enable: true,
            seconds: values.suppressionSeconds || 60,
          };
        } else {
          updatedData.suppression = {
            enable: false,
          };
        }

        updatedData.vlValidation = {
          enable: vlConfigReady && values.vlValidationEnable === true,
          promptTemplate: vlConfigReady && values.vlValidationEnable === true
            ? (values.vlValidationPromptTemplate || '').trim()
            : '',
        };

        // 保存 MQ 输出开关（默认开启）
        updatedData.publishToMq = values.publishToMq !== false;

        // 清理临时字段
        delete updatedData.triggerConditionEnable;
        delete updatedData.triggerConditionWindowSize;
        delete updatedData.triggerConditionMode;
        delete updatedData.triggerConditionThreshold;
        delete updatedData.suppressionEnable;
        delete updatedData.suppressionSeconds;
        delete updatedData.vlValidationEnable;
        delete updatedData.vlValidationPromptTemplate;
      } else if (nodeType === 'function') {
        // 自动从连线中识别上游算法节点
        const upstreamAlgorithmNodes = edges
          .filter(edge => edge.target === node.id)
          .map(edge => {
            const sourceNode = nodes.find(n => n.id === edge.source);
            return sourceNode;
          })
          .filter(n => n && (
            n.data?.type === 'algorithm' ||
            n.type === 'algorithm' ||
            n.data?.type === 'externalApi' ||
            n.type === 'externalApi' ||
            n.data?.type === 'function' ||
            n.type === 'function' ||
            n.data?.type === 'detectionFilter' ||
            n.data?.type === 'detection_filter' ||
            n.type === 'detectionFilter'
          ));

        const config = { ...(node.data?.config || {}) };

        config.function_name = values.functionName;

        // 自动保存上游节点ID
        if (upstreamAlgorithmNodes.length > 0) {
          config.input_a = {
            node_id: upstreamAlgorithmNodes[0].id,
            class_filter: values.classFilterA ? values.classFilterA.split(',').map((n: string) => parseInt(n.trim())) : []
          };
        }

        config.threshold = values.threshold;
        config.operator = values.operator;

        // 单输入函数列表
        const singleInputFunctions = [
          'height_ratio_frame',
          'width_ratio_frame',
          'area_ratio_frame',
          'size_absolute'
        ];

        // 双输入函数且有两个上游节点时，设置 input_b
        if (!singleInputFunctions.includes(values.functionName) && upstreamAlgorithmNodes.length > 1) {
          config.input_b = {
            node_id: upstreamAlgorithmNodes[1].id,
            class_filter: values.classFilterB ? values.classFilterB.split(',').map((n: string) => parseInt(n.trim())) : []
          };
        }

        // size_absolute 函数需要保存 dimension
        if (values.functionName === 'size_absolute') {
          config.dimension = values.dimension || 'height';
        }

        updatedData.config = config;

        // 保存所有上游节点ID列表到 data 中
        updatedData.input_nodes = upstreamAlgorithmNodes.map(n => n.id);

        // 清理临时字段
        delete updatedData.inputNodeA;
        delete updatedData.inputNodeB;
        delete updatedData.classFilterA;
        delete updatedData.classFilterB;
      } else if (nodeType === 'detectionFilter' || nodeType === 'detection_filter') {
        const unit = values.filterUnit || 'pixel';
        updatedData.config = {
          ...(node.data?.config || {}),
          dimension: values.filterDimension || 'height',
          unit,
          comparison: values.filterComparison || 'gte',
          threshold: unit === 'ratio'
            ? Number(values.filterThreshold || 0) / 100
            : Number(values.filterThreshold || 0),
        };
        delete updatedData.filterDimension;
        delete updatedData.filterUnit;
        delete updatedData.filterComparison;
        delete updatedData.filterThreshold;
      } else if (nodeType === 'condition') {
        // Condition 节点：保存条件配置
        updatedData.conditionKind = values.conditionKind || 'count';
        updatedData.targetCount = values.targetCount || 1;
        updatedData.comparisonType = values.comparisonType || '>=';
        updatedData.sourceNodeId = values.sourceNodeId;
        updatedData.textOperator = values.textOperator || 'contains';
        updatedData.patternType = values.patternType || 'keywords';
        updatedData.keywords = values.keywords || [];
        updatedData.keywordLogic = values.keywordLogic || 'any';
        updatedData.regexPattern = values.regexPattern || '';
        updatedData.caseSensitive = values.caseSensitive === true;
        updatedData.labels = values.labels || [];
        updatedData.windowSize = values.windowSize ?? 10;
        updatedData.direction = values.direction || 'both';
        updatedData.relativeThreshold = (values.relativeThresholdPercent ?? 50) / 100;
        updatedData.absoluteThreshold = values.absoluteThreshold ?? 3;
        updatedData.confirmationCount = values.confirmationCount ?? 1;
        updatedData.expression = values.httpExpression || {
          logic: 'and',
          children: [{ variable: '$success', operator: 'eq', value: true }],
        };
        delete updatedData.relativeThresholdPercent;
      } else if (nodeType === 'timeSchedule' || nodeType === 'time_schedule') {
        updatedData.weeklySchedule = normalizeWeeklySchedule(values.weeklySchedule);
      }

      if (version !== updateVersionRef.current) return;
      onUpdate(node.id, updatedData);
    } catch (error) {
      // 输入过程中的临时无效值（如未写完的 JSON）保留在表单中，待合法后再同步。
    }
  };

  const handleValuesChange = (changedValues: Record<string, unknown>) => {
    const changedFields = Object.keys(changedValues);
    if (changedFields.length === 1 && changedFields[0] === 'httpTestContext') {
      return;
    }
    if (
      changedFields.length > 0
      && changedFields.every((fieldName) => WEBHOOK_CREDENTIAL_FORM_FIELDS.has(fieldName))
    ) {
      return;
    }

    const version = updateVersionRef.current + 1;
    updateVersionRef.current = version;
    if (autoUpdateTimerRef.current) {
      clearTimeout(autoUpdateTimerRef.current);
    }
    // 让 shouldUpdate 内的条件字段先完成挂载，再收集和校验完整表单。
    autoUpdateTimerRef.current = setTimeout(() => {
      autoUpdateTimerRef.current = null;
      void handleUpdate(version);
    }, 0);
  };

  const commitWebhookCredentials = async () => {
    try {
      const values = await form.validateFields();
      const provider = values.webhookProvider || 'generic';
      const credentialPatch: WebhookCredentialPatch = {
        endpoint_url: String(values.webhookEndpointUrl || '').trim() || undefined,
        signing_secret: provider === 'dingtalk'
          ? String(values.webhookSigningSecret || '').trim() || undefined
          : undefined,
        device_key: provider === 'bark'
          ? String(values.webhookBarkDeviceKey || '').trim() || undefined
          : undefined,
      };
      // 写入型字段留空代表保留原配置，不能用空值或输入过程的前缀覆盖。
      if (!Object.values(credentialPatch).some(Boolean)) return;

      if (autoUpdateTimerRef.current) {
        clearTimeout(autoUpdateTimerRef.current);
        autoUpdateTimerRef.current = null;
      }
      updateVersionRef.current += 1;

      const config = buildWebhookConfig(
        values,
        node.data?.config || {},
        credentialPatch,
      );
      onUpdate(node.id, { config });
      form.setFieldsValue({
        webhookEndpointUrl: '',
        webhookSigningSecret: '',
        webhookBarkDeviceKey: '',
      });
    } catch {
      // Form.Item 会在字段下方展示校验错误，无效值不写入节点。
    }
  };

  const runHttpRequestTest = async () => {
    const requestVersion = ++httpTestVersionRef.current;
    const requestNodeId = node.id;
    const requestIsCurrent = () => (
      requestVersion === httpTestVersionRef.current
      && requestNodeId === activeNodeIdRef.current
    );
    setHttpTesting(true);
    setHttpTestResult(null);
    try {
      const values = await form.validateFields();
      if (!requestIsCurrent()) return;
      const config = buildHttpRequestConfig(values, node.data?.config || {});
      const context = JSON.parse(values.httpTestContext || '{}');
      const parsedWorkflowId = Number(workflowIdParam);
      const result = await testHttpRequest({
        workflow_id: workflowIdParam && Number.isInteger(parsedWorkflowId)
          ? parsedWorkflowId
          : undefined,
        node_id: node.id,
        config,
        context,
      });
      if (!requestIsCurrent()) return;
      setHttpTestResult(result);
      message.success(result?.result?.success ? '测试请求成功' : '测试请求已完成，请查看失败详情');
    } catch (error: any) {
      if (requestIsCurrent() && !error?.errorFields) {
        message.error(error?.data?.error || error?.message || '测试请求失败');
      }
    } finally {
      if (requestIsCurrent()) {
        setHttpTesting(false);
      }
    }
  };

  const getNodeConfigFields = () => {
    const nodeType = node.data?.type || node.type;
    console.log('getNodeConfigFields - 节点类型:', nodeType);
    console.log('当前 videoSourceId:', node.data.videoSourceId, '类型:', typeof node.data.videoSourceId);
    console.log('可用视频源:', videoSources);

    switch (nodeType) {
      case 'videoSource':
      case 'source':
        if (isTemplate) {
          return (
            <div className="info-box template-source-info">
              <InfoCircleOutlined />
              <div>
                <Text strong>视频源占位节点</Text>
                <div>模板无需选择具体视频源，复制为普通编排时自动绑定。</div>
              </div>
            </div>
          );
        }
        // 获取当前选中的视频源
        const currentSourceId = node.data.dataId ?? node.data.videoSourceId;
        const currentSource = videoSources.find(s => String(s.id) === String(currentSourceId));

        console.log('渲染视频源配置 -', {
          currentSourceId,
          currentSource: currentSource ? { name: currentSource.name, id: currentSource.id } : null,
          nodeDataKeys: Object.keys(node.data),
        });

        return (
          <>
            <div className="video-source-selector-trigger">
              {currentSource ? (
                <div className="current-source-card">
                  <div className="source-card-header">
                    <Space size="small">
                      <VideoCameraOutlined style={{ fontSize: 16, color: '#1890ff' }} />
                      <Text strong style={{ fontSize: 15 }}>
                        {currentSource.name}
                      </Text>
                    </Space>
                    <Button
                      type="primary"
                      size="small"
                      icon={<SearchOutlined />}
                      onClick={() => setSelectorVisible(true)}
                    >
                      重新选择
                    </Button>
                  </div>

                  <div className="source-card-details">
                    <div className="detail-item">
                      <span className="detail-label">编码:</span>
                      <span className="detail-value">{currentSource.source_code || '-'}</span>
                    </div>
                    <div className="detail-item">
                      <span className="detail-label">ID:</span>
                      <span className="detail-value">{currentSource.id}</span>
                    </div>
                    {currentSource.decoder_type && (
                      <div className="detail-item">
                        <span className="detail-label">解码器:</span>
                        <span className="detail-value">{currentSource.decoder_type}</span>
                      </div>
                    )}
                    {currentSource.url && (
                      <div className="detail-item">
                        <span className="detail-label">URL:</span>
                        <span className="detail-value url-text">{currentSource.url}</span>
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <Button
                  type="dashed"
                  block
                  size="large"
                  icon={<SearchOutlined />}
                  onClick={() => setSelectorVisible(true)}
                  style={{ height: 60, fontSize: 14 }}
                >
                  点击选择视频源
                </Button>
              )}
            </div>

            {/* 隐藏的表单项，用于验证和提交 */}
            <Form.Item
              name="videoSourceId"
              rules={[{ required: true, message: '请选择视频源' }]}
              hidden
            >
              <Input />
            </Form.Item>

            {videoSources.length === 0 && (
              <div className="info-box">
                <InfoCircleOutlined />
                <span>暂无可用视频源，请先在视频源管理中添加</span>
              </div>
            )}

            {currentSourceId && !currentSource && (
              <div className="info-box" style={{ background: '#fff7e6', borderColor: '#ffd591', color: '#d46b08' }}>
                <InfoCircleOutlined />
                <span>原视频源 (ID: {currentSourceId}) 不存在，请重新选择</span>
              </div>
            )}
          </>
        );

      case 'algorithm':
        return (
          <>
            {isVlAlgorithm ? (
              <div className="info-box" style={{ marginBottom: 16, background: '#f0fffe', borderColor: '#87e8de', color: '#006d75' }}>
                <InfoCircleOutlined />
                <span>此节点调用 VL 语义算法；提示词和模型参数在算法管理中统一维护。</span>
              </div>
            ) : null}
            {isOcrAlgorithm ? (
              <div className="info-box" style={{ marginBottom: 16, background: '#f0f7ff', borderColor: '#91caff', color: '#0958d9' }}>
                <InfoCircleOutlined />
                <span>此节点调用本地 OCR；检测与识别模型在算法管理中统一维护。</span>
              </div>
            ) : null}
            <Form.Item
              label={isOcrAlgorithm ? '文字置信度阈值' : '置信度阈值'}
              name="confidence"
            >
              <Select>
                <Option value={0.3}>0.3 (低)</Option>
                <Option value={0.5}>0.5 (中)</Option>
                <Option value={0.7}>0.7 (高)</Option>
                <Option value={0.9}>0.9 (极高)</Option>
              </Select>
            </Form.Item>

            <div className="form-divider" />

            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">执行配置</span>
              </div>

              <Form.Item
                label="检测间隔（秒）"
                name="intervalSeconds"
                extra="每N秒执行一次检测，1表示每帧都检测"
              >
                <InputNumber min={0.1} max={60} step={0.1} style={{ width: '100%' }} />
              </Form.Item>

              {isOcrAlgorithm ? (
                <Form.Item noStyle shouldUpdate={(previous, current) => previous.inputMode !== current.inputMode}>
                  {({ getFieldValue }) => {
                    const inputMode = getFieldValue('inputMode') || 'frame';
                    const cropDisabled = inputMode !== 'upstream_crops';
                    const hasAlgorithmIncoming = edges.some((edge) => {
                      if (edge.target !== node.id) return false;
                      const sourceNode = nodes.find((item) => item.id === edge.source);
                      const sourceType = sourceNode?.data?.type || sourceNode?.type;
                      return sourceType === 'algorithm';
                    });
                    return (
                      <>
                        {hasAlgorithmIncoming && inputMode === 'frame' ? (
                          <div className="info-box" style={{ marginBottom: 16, background: '#e6f4ff', borderColor: '#91caff', color: '#0958d9' }}>
                            <InfoCircleOutlined />
                            <span>若要只识别检测框内文字，请把连线设为『检测到』，并把 OCR 输入模式改为『上游裁剪』。</span>
                          </div>
                        ) : null}
                        <Form.Item
                          label="输入模式"
                          name="inputMode"
                          extra="整帧识别或仅对上游检测框裁剪识别"
                        >
                          <Select>
                            <Option value="frame">整帧</Option>
                            <Option value="upstream_crops">上游裁剪</Option>
                          </Select>
                        </Form.Item>
                        <Form.Item
                          label="扩边比例"
                          name="expandRatio"
                          extra="每边按框尺寸扩边，与组合检测一致"
                        >
                          <InputNumber min={0} max={1} step={0.05} disabled={cropDisabled} style={{ width: '100%' }} />
                        </Form.Item>
                        <Form.Item
                          label="最大候选"
                          name="maxCandidates"
                          extra="每帧最多裁剪识别的框数量，硬顶 32"
                        >
                          <InputNumber min={1} max={32} step={1} disabled={cropDisabled} style={{ width: '100%' }} />
                        </Form.Item>
                        <Form.Item
                          label="上游类别过滤"
                          name="upstreamClassFilter"
                          extra="留空不过滤；匹配 class_name / label / label_name"
                        >
                          <Select mode="tags" tokenSeparators={[',', '，']} disabled={cropDisabled} placeholder="全部类别" />
                        </Form.Item>
                      </>
                    );
                  }}
                </Form.Item>
              ) : null}

              {isVlAlgorithm ? (
                <>
                  <Form.Item
                    label="覆盖算法超时"
                    name="vlTimeoutOverrideEnabled"
                    valuePropName="checked"
                    extra={`关闭时使用算法配置中的 ${vlDefaultTimeout} 秒`}
                  >
                    <Switch />
                  </Form.Item>
                  <Form.Item noStyle shouldUpdate={(previous, current) => previous.vlTimeoutOverrideEnabled !== current.vlTimeoutOverrideEnabled}>
                    {({ getFieldValue }) => getFieldValue('vlTimeoutOverrideEnabled') ? (
                      <Form.Item
                        label="节点调用超时（秒）"
                        name="runtimeTimeout"
                        rules={[{ required: true, message: '请输入节点调用超时' }]}
                        extra="仅覆盖当前 workflow 节点"
                      >
                        <InputNumber min={1} max={300} style={{ width: '100%' }} />
                      </Form.Item>
                    ) : null}
                  </Form.Item>
                </>
              ) : isOcrAlgorithm ? null : (
                <Form.Item
                  label="运行超时（秒）"
                  name="runtimeTimeout"
                  extra="单次检测最大执行时间"
                >
                  <InputNumber min={1} max={300} style={{ width: '100%' }} />
                </Form.Item>
              )}

              {isVlAlgorithm || isOcrAlgorithm ? null : (
                <Form.Item
                  label="内存限制（MB）"
                  name="memoryLimitMb"
                  extra="算法运行最大内存使用"
                >
                  <InputNumber min={64} max={4096} step={64} style={{ width: '100%' }} />
                </Form.Item>
              )}

              <Form.Item
                label="标签名称"
                name="labelName"
                extra={<>支持 <code>{'{class}'}</code> 占位符，将替换为实际识别类别</>}
              >
                <Input placeholder="例如：目标-{class}" />
              </Form.Item>

              <Form.Item
                label="标签颜色"
                name="labelColor"
              >
                <Input type="color" style={{ width: 100 }} />
              </Form.Item>
            </div>

            {isTrackerEventAlgorithm ? (
              <>
                <div className="form-divider" />
                <div className="config-section">
                  <div className="config-section-header">
                    <span className="config-section-title">输出事件</span>
                  </div>
                  <Form.Item
                    label="事件类型"
                    name="trackEvent"
                    extra="在追踪结果上直接判定，不必再串徘徊/停留节点"
                  >
                    <Select>
                      <Option value="none">全部轨迹（只赋 ID）</Option>
                      <Option value="loiter">徘徊（区域内待够久）</Option>
                      <Option value="stay">停留（原地待够久）</Option>
                      <Option value="region_cross">穿越热区（按方向）</Option>
                    </Select>
                  </Form.Item>
                  <Form.Item noStyle shouldUpdate={(previous, current) => previous.trackEvent !== current.trackEvent}>
                    {({ getFieldValue }) => {
                      const event = getFieldValue('trackEvent') || 'none';
                      if (event === 'loiter' || event === 'stay') {
                        return (
                          <>
                            <Form.Item
                              label={event === 'stay' ? '停留多久（秒）' : '徘徊多久（秒）'}
                              name="minDwellSeconds"
                              extra="同一 ID 持续出现达到该时长才输出"
                            >
                              <InputNumber min={0.5} max={600} step={0.5} style={{ width: '100%' }} />
                            </Form.Item>
                            <Form.Item
                              label="最小位移（像素）"
                              name="minDisplacePx"
                              extra="活动半径低于该值不算。徘徊可用来排除站着不动；0 表示不限制"
                            >
                              <InputNumber min={0} max={4000} step={1} style={{ width: '100%' }} />
                            </Form.Item>
                            <Form.Item
                              label="最大位移（像素）"
                              name="maxDisplacePx"
                              extra={event === 'stay' ? '超出该半径视为在移动，不填默认 48' : '超出该半径不算徘徊；留空不限制'}
                            >
                              <InputNumber min={0} max={4000} step={1} style={{ width: '100%' }} placeholder={event === 'stay' ? '48' : '不限制'} />
                            </Form.Item>
                            <Form.Item
                              label="消失后遗忘（秒）"
                              name="disappearSeconds"
                              extra="目标丢失超过该时间后重新计时"
                            >
                              <InputNumber min={0.5} max={120} step={0.5} style={{ width: '100%' }} />
                            </Form.Item>
                          </>
                        );
                      }
                      if (event === 'region_cross') {
                        return (
                          <>
                            <Form.Item label="穿越方式" name="crossMode" extra="热区来自上游 ROI 绘制节点">
                              <Select>
                                <Option value="enter">进入热区</Option>
                                <Option value="exit">离开热区</Option>
                                <Option value="cross">进入或离开</Option>
                              </Select>
                            </Form.Item>
                            <Form.Item label="穿越方向" name="crossDirection">
                              <Select>
                                <Option value="any">任意方向</Option>
                                <Option value="left_to_right">从左到右</Option>
                                <Option value="right_to_left">从右到左</Option>
                                <Option value="top_to_bottom">从上到下</Option>
                                <Option value="bottom_to_top">从下到上</Option>
                              </Select>
                            </Form.Item>
                            <Form.Item
                              label="消失后遗忘（秒）"
                              name="disappearSeconds"
                              extra="目标丢失超过该时间后重新检测穿越"
                            >
                              <InputNumber min={0.5} max={120} step={0.5} style={{ width: '100%' }} />
                            </Form.Item>
                          </>
                        );
                      }
                      return null;
                    }}
                  </Form.Item>
                </div>
              </>
            ) : null}
          </>
        );

      case 'externalApi':
      case 'external_api':
        return (
          <>
            <Form.Item
              label="选择 API"
              name="externalApiId"
              rules={[{ required: true, message: '请选择外部 API' }]}
            >
              <Select placeholder="选择已配置的外部 API">
                {externalApis.map((item) => (
                  <Option key={item.id} value={item.id}>
                    {item.name}
                  </Option>
                ))}
              </Select>
            </Form.Item>

            {externalApis.length === 0 && (
              <div className="info-box" style={{ marginBottom: 16 }}>
                <InfoCircleOutlined />
                <span>暂无可用外部 API，请先在“外部 API”页面中配置</span>
              </div>
            )}

            <div className="form-divider" />

            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">执行配置</span>
              </div>

              <Form.Item
                label="执行模式"
                name="executionMode"
                extra="同步等待会阻塞当前节点；异步提交只投递请求，不等待结果"
              >
                <Select>
                  <Option value="sync">同步等待</Option>
                  <Option value="async_submit">异步提交</Option>
                </Select>
              </Form.Item>

              <Form.Item
                label="调用间隔（秒）"
                name="intervalSeconds"
              >
                <InputNumber min={0.1} max={300} step={0.1} style={{ width: '100%' }} />
              </Form.Item>

              <Form.Item
                label="超时（秒）"
                name="timeoutSeconds"
              >
                <InputNumber min={1} max={300} step={1} style={{ width: '100%' }} />
              </Form.Item>

              <Form.Item label="包含图片" name="includeImage" valuePropName="checked">
                <Switch />
              </Form.Item>

              <Form.Item label="包含上游结果" name="includeUpstreamResults" valuePropName="checked">
                <Switch />
              </Form.Item>
            </div>

            <div className="form-divider" />

            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">输入参数</span>
              </div>
              <div className="info-box" style={{ marginBottom: 12 }}>
                <InfoCircleOutlined />
                <span>系统会自动注入 `workflow_id`、`node_id`、`frame_timestamp`，可选注入 `image_base64` 和 `upstream_results`。</span>
              </div>
              <Form.Item
                label="附加请求体"
                name="payloadTemplate"
                extra="JSON 对象，会与系统自动生成的字段合并"
              >
                <TextArea rows={6} placeholder='{"scene":"gate-1"}' />
              </Form.Item>
            </div>

            <div className="form-divider" />

            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">输出参数</span>
              </div>
              <DetectionResponseContract />
              <Form.Item
                label="输出映射"
                name="outputMapping"
                extra="请用点路径声明返回值位置。支持 has_detection_path / detections_path / metadata_path / label_color_path"
              >
                <TextArea rows={6} placeholder='{"detections_path":"data.detections"}' />
              </Form.Item>
            </div>
          </>
        );

      case 'httpRequest':
      case 'http_request':
        return (
          <>
            <div className="info-box" style={{ marginBottom: 16, background: '#f0fbfb', borderColor: '#87e8de', color: '#006d75' }}>
              <InfoCircleOutlined />
              <span>使用 {'{{ $.source.code }}'} 引用系统字段，或用 {'{{ $.nodes["node-id"].outputs.name }}'} 引用上游结果。</span>
            </div>

            <div className="config-section">
              <div className="config-section-header"><span className="config-section-title">请求</span></div>
              <Form.Item label="请求方法" name="httpMethod" rules={[{ required: true }]}>
                <Select options={['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((value) => ({ value, label: value }))} />
              </Form.Item>
              <Form.Item
                label="URL"
                name="httpUrl"
                rules={[
                  { required: true, message: '请输入 HTTP/HTTPS URL' },
                  { pattern: /^(https?:\/\/|\s*\{\{\s*\$)/i, message: 'URL 必须以 http://、https:// 或完整 JSONPath 模板开头' },
                ]}
              >
                <Input placeholder="https://api.example.com/status" />
              </Form.Item>
              <Space size={12} style={{ width: '100%' }}>
                <Form.Item label="调用间隔（秒）" name="httpIntervalSeconds" rules={[{ required: true }]} style={{ flex: 1 }}>
                  <InputNumber min={0.1} max={300} step={0.1} style={{ width: '100%' }} />
                </Form.Item>
                <Form.Item label="超时（秒）" name="httpTimeoutSeconds" rules={[{ required: true }]} style={{ flex: 1 }}>
                  <InputNumber min={1} max={300} style={{ width: '100%' }} />
                </Form.Item>
              </Space>
            </div>

            <div className="form-divider" />
            <div className="config-section">
              <div className="config-section-header"><span className="config-section-title">Query 参数</span></div>
              <Form.List name="httpQueryParams">
                {(fields, { add, remove }) => (
                  <div className="http-config-list">
                    {fields.map(({ key, name: fieldName, ...restField }) => (
                      <div className="http-config-row" key={key}>
                        <Form.Item {...restField} name={[fieldName, 'enabled']} valuePropName="checked" initialValue>
                          <Switch size="small" />
                        </Form.Item>
                        <Form.Item {...restField} name={[fieldName, 'name']} rules={[{ required: true, message: '名称不能为空' }]}>
                          <Input placeholder="参数名" />
                        </Form.Item>
                        <Form.Item {...restField} name={[fieldName, 'value']}>
                          <Input.Password placeholder="值或模板" visibilityToggle={false} />
                        </Form.Item>
                        <Form.Item {...restField} name={[fieldName, 'sensitive']} valuePropName="checked">
                          <Switch size="small" checkedChildren="敏感" unCheckedChildren="普通" />
                        </Form.Item>
                        <Button type="text" danger icon={<MinusCircleOutlined />} onClick={() => remove(fieldName)} aria-label="删除 Query 参数" />
                      </div>
                    ))}
                    <Button block type="dashed" icon={<PlusOutlined />} onClick={() => add({ enabled: true, name: '', value: '', sensitive: false })}>添加 Query 参数</Button>
                  </div>
                )}
              </Form.List>
            </div>

            <div className="form-divider" />
            <div className="config-section">
              <div className="config-section-header"><span className="config-section-title">请求头</span></div>
              <Form.List name="httpHeaders">
                {(fields, { add, remove }) => (
                  <div className="http-config-list">
                    {fields.map(({ key, name: fieldName, ...restField }) => (
                      <div className="http-config-row" key={key}>
                        <Form.Item {...restField} name={[fieldName, 'enabled']} valuePropName="checked" initialValue>
                          <Switch size="small" />
                        </Form.Item>
                        <Form.Item {...restField} name={[fieldName, 'name']} rules={[{ required: true, message: '名称不能为空' }]}>
                          <Input placeholder="Authorization" />
                        </Form.Item>
                        <Form.Item {...restField} name={[fieldName, 'value']}>
                          <Input.Password placeholder="值或模板；留空保持敏感值" visibilityToggle={false} />
                        </Form.Item>
                        <Form.Item {...restField} name={[fieldName, 'sensitive']} valuePropName="checked">
                          <Switch size="small" checkedChildren="敏感" unCheckedChildren="普通" />
                        </Form.Item>
                        <Button type="text" danger icon={<MinusCircleOutlined />} onClick={() => remove(fieldName)} aria-label="删除请求头" />
                      </div>
                    ))}
                    <Button block type="dashed" icon={<PlusOutlined />} onClick={() => add({ enabled: true, name: '', value: '', sensitive: false })}>添加请求头</Button>
                  </div>
                )}
              </Form.List>

              <Form.Item noStyle shouldUpdate={(previous, current) => previous.httpMethod !== current.httpMethod}>
                {({ getFieldValue }) => getFieldValue('httpMethod') === 'GET' ? null : (
                  <Form.Item
                    label="JSON 请求体"
                    name="httpJsonBody"
                    rules={[{
                      validator: (_, value) => {
                        try {
                          const parsed = JSON.parse(value || 'null');
                          return parsed === null || Array.isArray(parsed) || typeof parsed === 'object'
                            ? Promise.resolve()
                            : Promise.reject(new Error('请求体必须是 JSON 对象、数组或 null'));
                        } catch {
                          return Promise.reject(new Error('JSON 请求体格式无效'));
                        }
                      },
                    }]}
                  >
                    <TextArea rows={7} placeholder='{"camera":"{{ $.source.code }}"}' />
                  </Form.Item>
                )}
              </Form.Item>
            </div>

            <div className="form-divider" />
            <div className="config-section">
              <div className="config-section-header"><span className="config-section-title">JSONPath 输出</span></div>
              <Form.List name="httpExtractors">
                {(fields, { add, remove }) => (
                  <div className="http-config-list">
                    {fields.map(({ key, name: fieldName, ...restField }) => (
                      <div className="http-config-row extractor-row" key={key}>
                        <Form.Item {...restField} name={[fieldName, 'name']} rules={[{ required: true }, { pattern: /^[A-Za-z_][A-Za-z0-9_]*$/, message: '仅支持字母、数字和下划线，且不能以数字开头' }]}>
                          <Input placeholder="risk_score" />
                        </Form.Item>
                        <Form.Item {...restField} name={[fieldName, 'jsonpath']} rules={[{ required: true }, { pattern: /^\$/, message: 'JSONPath 必须以 $ 开头' }]}>
                          <Input placeholder="$.data.score" />
                        </Form.Item>
                        <Form.Item {...restField} name={[fieldName, 'required']} valuePropName="checked">
                          <Switch size="small" checkedChildren="必填" unCheckedChildren="可选" />
                        </Form.Item>
                        <Button type="text" danger icon={<MinusCircleOutlined />} onClick={() => remove(fieldName)} aria-label="删除提取规则" />
                      </div>
                    ))}
                    <Button block type="dashed" icon={<PlusOutlined />} onClick={() => add({ name: '', jsonpath: '$.', required: false })}>添加提取变量</Button>
                  </div>
                )}
              </Form.List>
            </div>

            <div className="form-divider" />
            <div className="config-section http-test-console">
              <div className="config-section-header"><span className="config-section-title">测试请求</span></div>
              <Form.Item label="模拟上下文 JSON" name="httpTestContext">
                <TextArea rows={5} />
              </Form.Item>
              <Button type="primary" icon={<ExperimentOutlined />} loading={httpTesting} onClick={() => void runHttpRequestTest()}>
                发送测试请求
              </Button>
              {httpTestResult ? <pre className="http-test-result">{JSON.stringify(httpTestResult, null, 2)}</pre> : null}
            </div>
          </>
        );

      case 'webhook': {
        const webhookConfig = node.data?.config || {};
        const providerOptions = webhookConfig.provider_options || {};
        const endpointConfigured = Boolean(webhookConfig.endpoint_url || webhookConfig.endpoint_url_configured);
        const signingSecretConfigured = Boolean(providerOptions.signing_secret || providerOptions.signing_secret_configured);
        const barkKeyConfigured = Boolean(providerOptions.device_key || providerOptions.device_key_configured);
        const validateJson = (expected: 'object' | 'array') => (_: any, value: string) => {
          try {
            const parsed = JSON.parse(value || (expected === 'array' ? '[]' : '{}'));
            const valid = expected === 'array'
              ? Array.isArray(parsed)
              : Boolean(parsed) && typeof parsed === 'object' && !Array.isArray(parsed);
            return valid
              ? Promise.resolve()
              : Promise.reject(new Error(expected === 'array' ? '必须是 JSON 数组' : '必须是 JSON 对象'));
          } catch {
            return Promise.reject(new Error('JSON 格式无效'));
          }
        };

        return (
          <>
            <div className="info-box" style={{ marginBottom: 16 }}>
              <InfoCircleOutlined />
              <span>仅在上游告警真正落库后异步推送；工作流测试只生成预览，不访问外部端点。</span>
            </div>

            <Form.Item label="协议" name="webhookProvider">
              <Select>
                <Option value="generic">通用 JSON Webhook</Option>
                <Option value="dingtalk">钉钉自定义机器人</Option>
                <Option value="bark">Bark</Option>
              </Select>
            </Form.Item>

            <Form.Item
              label="端点地址"
              name="webhookEndpointUrl"
              rules={[{
                validator: (_, value) => value || (
                  endpointConfigured
                  && (form.getFieldValue('webhookProvider') || 'generic') === (webhookConfig.provider || 'generic')
                )
                  ? Promise.resolve()
                  : Promise.reject(new Error('请输入 Webhook 端点地址')),
              }]}
              extra={endpointConfigured && !webhookConfig.endpoint_url
                ? `已配置：${webhookConfig.endpoint_display || '******'}；留空保持原值`
                : '仅支持 HTTP/HTTPS；钉钉必须使用官方机器人地址'}
            >
              <Input.Password
                placeholder={endpointConfigured ? '留空保持已配置地址' : 'https://...'}
                onBlur={() => void commitWebhookCredentials()}
              />
            </Form.Item>

            <Form.Item noStyle shouldUpdate={(previous, current) => previous.webhookProvider !== current.webhookProvider}>
              {({ getFieldValue }) => {
                const provider = getFieldValue('webhookProvider') || 'generic';
                if (provider === 'dingtalk') {
                  return (
                    <Form.Item
                      label="加签密钥"
                      name="webhookSigningSecret"
                      extra={signingSecretConfigured ? '已配置；留空保持原值' : '可选，对应钉钉机器人 SEC 密钥'}
                    >
                      <Input.Password
                        placeholder={signingSecretConfigured ? '留空保持原值' : 'SEC...'}
                        onBlur={() => void commitWebhookCredentials()}
                      />
                    </Form.Item>
                  );
                }
                if (provider === 'bark') {
                  return (
                    <>
                      <Form.Item
                        label="Device Key"
                        name="webhookBarkDeviceKey"
                        rules={[{
                          validator: (_, value) => value || barkKeyConfigured
                            ? Promise.resolve()
                            : Promise.reject(new Error('请输入 Bark Device Key')),
                        }]}
                        extra={barkKeyConfigured ? '已配置；留空保持原值' : undefined}
                      >
                        <Input.Password
                          placeholder={barkKeyConfigured ? '留空保持原值' : 'Bark Device Key'}
                          onBlur={() => void commitWebhookCredentials()}
                        />
                      </Form.Item>
                      <Form.Item label="通知分组" name="webhookBarkGroup">
                        <Input placeholder="VideoBA" />
                      </Form.Item>
                      <Form.Item label="中断级别" name="webhookBarkLevel">
                        <Select>
                          <Option value="active">active</Option>
                          <Option value="timeSensitive">timeSensitive</Option>
                          <Option value="passive">passive</Option>
                          <Option value="critical">critical</Option>
                        </Select>
                      </Form.Item>
                      <Form.Item label="提示音" name="webhookBarkSound">
                        <Input placeholder="可选，例如 alarm" />
                      </Form.Item>
                    </>
                  );
                }
                return null;
              }}
            </Form.Item>

            <div className="form-divider" />

            <Form.Item label="标题模板" name="webhookTitleTemplate">
              <Input placeholder="【{{alert.level}}】{{alert.type}}" />
            </Form.Item>
            <Form.Item
              label="正文模板"
              name="webhookBodyTemplate"
              extra="支持 {{alert.message}}、{{source.name}}、{{detection.detections}} 等点路径占位符"
            >
              <TextArea rows={4} />
            </Form.Item>
            <Form.Item label="附带媒体 URL" name="webhookIncludeMediaUrls" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item
              label="媒体公共基地址"
              name="webhookPublicBaseUrl"
              extra="可选节点级覆盖；留空时继承系统设置中的全局公共访问地址"
            >
              <Input placeholder="https://video.example.com" />
            </Form.Item>

            <div className="form-divider" />

            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">投递策略</span>
              </div>
              <Form.Item label="单次超时（秒）" name="webhookTimeoutSeconds">
                <InputNumber min={1} max={30} style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item label="最多尝试次数" name="webhookMaxAttempts">
                <InputNumber min={1} max={5} style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item label="初始退避（秒）" name="webhookRetryBackoffSeconds">
                <InputNumber min={0.1} max={30} step={0.1} style={{ width: '100%' }} />
              </Form.Item>
            </div>

            <div className="form-divider" />

            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">高级请求配置</span>
              </div>
              <Form.Item
                label="请求头"
                name="webhookHeaders"
                rules={[{ validator: validateJson('array') }]}
                extra={'JSON 数组：[{"name":"Authorization","value":"Bearer ...","sensitive":true}]'}
              >
                <TextArea rows={6} />
              </Form.Item>
              <Form.Item noStyle shouldUpdate={(previous, current) => previous.webhookProvider !== current.webhookProvider}>
                {({ getFieldValue }) => getFieldValue('webhookProvider') === 'generic' ? (
                  <Form.Item
                    label="请求体模板"
                    name="webhookPayloadTemplate"
                    rules={[{ validator: validateJson('object') }]}
                    extra="留空对象时发送完整标准事件；支持模板占位符"
                  >
                    <TextArea rows={8} placeholder='{"event_id":"{{event_id}}","alert":"{{alert}}"}' />
                  </Form.Item>
                ) : null}
              </Form.Item>
            </div>
          </>
        );
      }

      case 'condition': {
        const upstreamResultNodes = edges
          .filter(edge => edge.target === node.id)
          .map(edge => nodes.find(item => item.id === edge.source))
          .filter(sourceNode => {
            const sourceType = sourceNode?.data?.type || sourceNode?.type;
            return [
              'algorithm', 'function', 'externalApi', 'external_api',
              'detectionFilter', 'detection_filter',
            ].includes(sourceType);
          });
        const upstreamOcrNodes = upstreamResultNodes
          .filter(sourceNode => {
            if (!sourceNode || (sourceNode.data?.type || sourceNode.type) !== 'algorithm') return false;
            const sourceAlgorithm = algorithms.find(item =>
              String(item.id) === String(sourceNode.data?.dataId ?? sourceNode.data?.algorithmId));
            return (sourceNode.data?.algorithmType || sourceAlgorithm?.algorithm_type) === 'ocr';
          });
        const upstreamHttpNodes = edges
          .filter(edge => edge.target === node.id)
          .map(edge => nodes.find(item => item.id === edge.source))
          .filter(sourceNode => {
            const sourceType = sourceNode?.data?.type || sourceNode?.type;
            return sourceType === 'httpRequest' || sourceType === 'http_request';
          });
        return (
          <>
            <div className="info-box" style={{ marginBottom: 16 }}>
              <InfoCircleOutlined />
              <span>条件节点根据检测结果、OCR 文字或 HTTP 命名变量控制 yes/no 分支</span>
            </div>

            <Form.Item
              label="条件类型"
              name="conditionKind"
              initialValue="count"
            >
              <Select>
                <Option value="count">目标数量</Option>
                <Option value="count_change">数量骤变</Option>
                <Option value="ocr_text">OCR 文字</Option>
                <Option value="http_value">API 值条件</Option>
              </Select>
            </Form.Item>

            <Form.Item noStyle shouldUpdate={(previous, current) => (
              previous.conditionKind !== current.conditionKind
              || previous.sourceNodeId !== current.sourceNodeId
            )}>
              {({ getFieldValue }) => getFieldValue('conditionKind') === 'http_value' ? (
                <>
                  <Form.Item
                    label="HTTP 来源节点"
                    name="sourceNodeId"
                    rules={[{ required: true, message: '请选择已连接的 HTTP 请求节点' }]}
                  >
                    <Select placeholder="选择上游 HTTP 请求节点">
                      {upstreamHttpNodes.map(sourceNode => (
                        <Option key={sourceNode!.id} value={sourceNode!.id}>{sourceNode!.data?.label || sourceNode!.id}</Option>
                      ))}
                    </Select>
                  </Form.Item>
                  {upstreamHttpNodes.length === 0 ? (
                    <div className="info-box" style={{ marginBottom: 16, background: '#fff7e6', borderColor: '#ffd591', color: '#ad4e00' }}>
                      <InfoCircleOutlined />
                      <span>请先将一个 HTTP 请求节点连接到当前条件节点。</span>
                    </div>
                  ) : null}
                  <Form.Item
                    label="AND / OR 条件组"
                    name="httpExpression"
                    rules={[{
                      validator: (_, value) => value?.children?.length
                        ? Promise.resolve()
                        : Promise.reject(new Error('至少添加一条条件规则')),
                    }]}
                  >
                    <HttpConditionBuilder
                      variables={(() => {
                        const source = upstreamHttpNodes.find(item => item?.id === getFieldValue('sourceNodeId'));
                        const extractors = source?.data?.config?.extractors;
                        return Array.isArray(extractors)
                          ? extractors.map((item: any) => item.name).filter(Boolean)
                          : [];
                      })()}
                    />
                  </Form.Item>
                  <div className="info-box">
                    <InfoCircleOutlined />
                    <span>比较值按 JSON 强类型处理：数字 1 不等于字符串 "1"。内置变量可判断请求成功、状态码和错误类型。</span>
                  </div>
                </>
              ) : getFieldValue('conditionKind') === 'ocr_text' ? (
                <>
                  <Form.Item
                    label="OCR 来源节点"
                    name="sourceNodeId"
                    rules={[{ required: true, message: '请选择已连接的 OCR 节点' }]}
                  >
                    <Select placeholder="选择上游 OCR 节点">
                      {upstreamOcrNodes.map(sourceNode => (
                        <Option key={sourceNode.id} value={sourceNode.id}>{sourceNode.data?.label || sourceNode.id}</Option>
                      ))}
                    </Select>
                  </Form.Item>
                  {upstreamOcrNodes.length === 0 ? (
                    <div className="info-box" style={{ marginBottom: 16, background: '#fff7e6', borderColor: '#ffd591', color: '#ad4e00' }}>
                      <InfoCircleOutlined />
                      <span>请先将 OCR 算法节点连接到当前条件节点。</span>
                    </div>
                  ) : null}
                  <Form.Item label="判断方式" name="textOperator" initialValue="contains">
                    <Select>
                      <Option value="contains">包含</Option>
                      <Option value="not_contains">不包含</Option>
                    </Select>
                  </Form.Item>
                  <Form.Item label="匹配方式" name="patternType" initialValue="keywords">
                    <Select>
                      <Option value="keywords">关键词</Option>
                      <Option value="regex">正则表达式</Option>
                    </Select>
                  </Form.Item>
                  <Form.Item noStyle shouldUpdate={(previous, current) => previous.patternType !== current.patternType}>
                    {({ getFieldValue: getPatternType }) => getPatternType('patternType') === 'regex' ? (
                      <Form.Item
                        label="正则表达式"
                        name="regexPattern"
                        rules={[
                          { required: true, message: '请输入正则表达式' },
                          {
                            validator: (_, value) => {
                              if (!value) return Promise.resolve();
                              try {
                                new RegExp(value);
                                return Promise.resolve();
                              } catch {
                                return Promise.reject(new Error('正则表达式无效'));
                              }
                            },
                          },
                        ]}
                      >
                        <Input placeholder="例如：车牌[A-Z0-9]{6}" />
                      </Form.Item>
                    ) : (
                      <>
                        <Form.Item
                          label="关键词"
                          name="keywords"
                          rules={[{ required: true, type: 'array', min: 1, message: '至少输入一个关键词' }]}
                          extra="输入关键词后按回车确认"
                        >
                          <Select mode="tags" tokenSeparators={[',', '，']} placeholder="输入关键词" />
                        </Form.Item>
                        <Form.Item label="关键词逻辑" name="keywordLogic" initialValue="any">
                          <Select>
                            <Option value="any">任一关键词命中</Option>
                            <Option value="all">全部关键词命中</Option>
                          </Select>
                        </Form.Item>
                      </>
                    )}
                  </Form.Item>
                  <Form.Item label="区分大小写" name="caseSensitive" valuePropName="checked">
                    <Switch />
                  </Form.Item>
                </>
              ) : getFieldValue('conditionKind') === 'count_change' ? (
                <>
                  <Form.Item
                    label="统计来源节点"
                    name="sourceNodeId"
                    rules={[{ required: true, message: '请选择已连接的上游结果节点' }]}
                  >
                    <Select placeholder="选择上游算法、函数或外部 API 节点">
                      {upstreamResultNodes.map(sourceNode => (
                        <Option key={sourceNode.id} value={sourceNode.id}>
                          {sourceNode.data?.label || sourceNode.id}
                        </Option>
                      ))}
                    </Select>
                  </Form.Item>
                  {upstreamResultNodes.length === 0 ? (
                    <div className="info-box" style={{ marginBottom: 16, background: '#fff7e6', borderColor: '#ffd591', color: '#ad4e00' }}>
                      <InfoCircleOutlined />
                      <span>请先连接一个算法、函数或外部 API 节点。</span>
                    </div>
                  ) : null}
                  <Form.Item
                    label="目标类别"
                    name="labels"
                    extra="留空统计全部目标；输入类别后按回车确认，不区分大小写"
                  >
                    <Select mode="tags" tokenSeparators={[',', '，']} placeholder="全部类别" />
                  </Form.Item>
                  <Form.Item
                    label="历史窗口（次）"
                    name="windowSize"
                    rules={[{ required: true }, { type: 'number', min: 2, max: 300 }]}
                    extra="积满历史样本后，从下一次检测开始判断"
                  >
                    <InputNumber min={2} max={300} step={1} style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item label="变化方向" name="direction" rules={[{ required: true }]}>
                    <Select>
                      <Option value="both">骤增或骤减</Option>
                      <Option value="increase">仅骤增</Option>
                      <Option value="decrease">仅骤减</Option>
                    </Select>
                  </Form.Item>
                  <Form.Item
                    label="相对变化阈值（%）"
                    name="relativeThresholdPercent"
                    rules={[{ required: true }, { type: 'number', min: 0.01, max: 10000 }]}
                  >
                    <InputNumber min={0.01} max={10000} step={5} style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item
                    label="最小绝对变化（个）"
                    name="absoluteThreshold"
                    rules={[{ required: true }, { type: 'number', min: 1, max: 100000 }]}
                    extra="相对阈值和绝对阈值需同时满足"
                  >
                    <InputNumber min={1} max={100000} step={1} style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item
                    label="连续确认次数"
                    name="confirmationCount"
                    rules={[{ required: true }, { type: 'number', min: 1, max: 20 }]}
                    extra="触发后采用边沿语义，恢复相同次数后重新布防"
                  >
                    <InputNumber min={1} max={20} step={1} style={{ width: '100%' }} />
                  </Form.Item>
                </>
              ) : (
                <>
                  <Form.Item label="比较类型" name="comparisonType">
                    <Select>
                      <Option value=">=">至少N个 (≥)</Option>
                      <Option value="==">正好N个 (=)</Option>
                    </Select>
                  </Form.Item>
                  <Form.Item
                    label="数量阈值"
                    name="targetCount"
                    rules={[
                      { required: true, message: '请输入数量阈值' },
                      { type: 'number', min: 1, max: 1000, message: '请输入1-1000之间的数字' }
                    ]}
                  >
                    <InputNumber min={1} max={1000} step={1} style={{ width: '100%' }} />
                  </Form.Item>
                </>
              )}
            </Form.Item>
          </>
        );
      }

      case 'timeSchedule':
      case 'time_schedule':
        return (
          <>
            <div className="info-box" style={{ marginBottom: 16, background: '#f0f5ff', borderColor: '#adc6ff', color: '#1d39c4' }}>
              <InfoCircleOutlined />
              <span>按服务器本地时区判断。起止分钟均包含，跨日时段请拆成两天配置。</span>
            </div>
            <Form.Item
              label="每周启用时段"
              name="weeklySchedule"
              rules={[{
                validator: (_, rawValue: WeeklySchedule) => {
                  const schedule = normalizeWeeklySchedule(rawValue);
                  let periodCount = 0;
                  for (const { key, label } of WEEKDAYS) {
                    for (const period of schedule[key]) {
                      periodCount += 1;
                      if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(period.start || '') || !/^([01]\d|2[0-3]):[0-5]\d$/.test(period.end || '')) {
                        return Promise.reject(new Error(`${label}存在未填写或格式错误的时间`));
                      }
                      if (period.start > period.end) {
                        return Promise.reject(new Error(`${label}存在跨日时段，请拆成两天配置`));
                      }
                    }
                  }
                  return periodCount > 0
                    ? Promise.resolve()
                    : Promise.reject(new Error('至少需要配置一个启用时段'));
                },
              }]}
            >
              <TimeScheduleEditor />
            </Form.Item>
          </>
        );

      case 'detectionFilter':
      case 'detection_filter': {
        const filterUpstreamEdges = edges.filter(edge => edge.target === node.id);
        return (
          <>
            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">输入状态</span>
              </div>
              <div className="info-box" style={{
                background: filterUpstreamEdges.length === 1 ? '#f6ffed' : '#fff7e6',
                borderColor: filterUpstreamEdges.length === 1 ? '#b7eb8f' : '#ffd591',
                color: filterUpstreamEdges.length === 1 ? '#389e0d' : '#d46b08',
              }}>
                <InfoCircleOutlined />
                <span>
                  {filterUpstreamEdges.length === 1
                    ? '已连接一个上游检测结果节点'
                    : '请连接一个算法、外部 API、函数或尺寸筛选节点'}
                </span>
              </div>
            </div>

            <div className="form-divider" />

            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">尺寸保留规则</span>
              </div>

              <Form.Item label="检测维度" name="filterDimension" rules={[{ required: true }]}>
                <Select>
                  <Option value="height">高度</Option>
                  <Option value="width">宽度</Option>
                </Select>
              </Form.Item>

              <Form.Item label="尺寸单位" name="filterUnit" rules={[{ required: true }]}>
                <Select>
                  <Option value="pixel">像素</Option>
                  <Option value="ratio">占画面比例</Option>
                </Select>
              </Form.Item>

              <Form.Item label="保留条件" name="filterComparison" rules={[{ required: true }]}>
                <Select>
                  <Option value="gte">大于等于（最小值）</Option>
                  <Option value="lte">小于等于（最大值）</Option>
                </Select>
              </Form.Item>

              <Form.Item noStyle shouldUpdate={(previous, current) => previous.filterUnit !== current.filterUnit}>
                {({ getFieldValue }) => {
                  const unit = getFieldValue('filterUnit') || 'pixel';
                  return (
                    <Form.Item
                      label={unit === 'ratio' ? '阈值（%）' : '阈值（px）'}
                      name="filterThreshold"
                      extra={unit === 'ratio'
                        ? '以目标高度/画面高度或目标宽度/画面宽度计算'
                        : '按检测框在原始画面中的像素尺寸计算'}
                      rules={[
                        { required: true, message: '请输入尺寸阈值' },
                        {
                          type: 'number',
                          min: 0,
                          max: unit === 'ratio' ? 100 : 10000000,
                          message: unit === 'ratio' ? '请输入 0–100 之间的比例' : '请输入非负像素值',
                        },
                      ]}
                    >
                      <InputNumber
                        min={0}
                        max={unit === 'ratio' ? 100 : 10000000}
                        step={unit === 'ratio' ? 0.1 : 1}
                        precision={unit === 'ratio' ? 2 : 0}
                        addonAfter={unit === 'ratio' ? '%' : 'px'}
                        style={{ width: '100%' }}
                      />
                    </Form.Item>
                  );
                }}
              </Form.Item>
            </div>
          </>
        );
      }

      case 'function':
        // 自动识别上游算法节点
        const getUpstreamAlgorithmNodes = () => {
          const upstreamNodes = edges
            .filter(edge => edge.target === node.id)
            .map(edge => {
              const sourceNode = nodes.find(n => n.id === edge.source);
              return sourceNode;
            })
            .filter(n => n && (
              n.data?.type === 'algorithm' ||
              n.type === 'algorithm' ||
              n.data?.type === 'externalApi' ||
              n.type === 'externalApi' ||
              n.data?.type === 'function' ||
              n.type === 'function' ||
              n.data?.type === 'detectionFilter' ||
              n.data?.type === 'detection_filter' ||
              n.type === 'detectionFilter'
            ));

          return upstreamNodes;
        };

        const upstreamNodes = getUpstreamAlgorithmNodes();

        return (
          <>
            {/* 输入配置 */}
            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">输入配置（自动识别）</span>
              </div>

              {upstreamNodes.length === 0 ? (
                <div className="info-box" style={{ background: '#fff7e6', borderColor: '#ffd591', color: '#d46b08' }}>
                  <InfoCircleOutlined />
                  <span>请先从上游算法节点连线到当前函数节点</span>
                </div>
              ) : (
                <>
                  <div className="info-box" style={{ background: '#f6ffed', borderColor: '#b7eb8f', color: '#52c41a', marginBottom: 12 }}>
                    <InfoCircleOutlined />
                    <span>已自动识别 {upstreamNodes.length} 个上游算法节点</span>
                  </div>

                  {upstreamNodes.map((upstreamNode, index) => {
                    const letter = index === 0 ? 'A' : 'B';
                    return (
                      <div key={upstreamNode.id} style={{ marginBottom: 16 }}>
                        <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8, color: '#262626' }}>
                          输入节点{letter}：{upstreamNode.data?.label || upstreamNode.id}
                        </div>

                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                          <Tag color="blue">{upstreamNode.id}</Tag>
                          <span style={{ fontSize: 12, color: '#8c8c8c' }}>
                            {upstreamNode.data?.dataId || 'N/A'}
                          </span>
                        </div>

                        <Form.Item
                          label={`类别过滤${letter}`}
                          name={index === 0 ? 'classFilterA' : 'classFilterB'}
                          style={{ marginBottom: 0 }}
                        >
                          <Input placeholder={`如: 0,1,2 (留空表示全部类别)`} />
                        </Form.Item>

                        {index < upstreamNodes.length - 1 && <div className="form-divider" style={{ margin: '12px 0' }} />}
                      </div>
                    );
                  })}

                  {/* 单输入函数但连接了多个节点时的警告 */}
                  <Form.Item noStyle shouldUpdate={(prevValues, currentValues) => prevValues.functionName !== currentValues.functionName}>
                    {({ getFieldValue }) => {
                      const functionName = getFieldValue('functionName') || 'area_ratio';
                      const singleInputFunctions = [
                        'height_ratio_frame',
                        'width_ratio_frame',
                        'area_ratio_frame',
                        'size_absolute'
                      ];

                      if (singleInputFunctions.includes(functionName) && upstreamNodes.length > 1) {
                        return (
                          <div className="info-box" style={{ marginTop: 12, background: '#fff7e6', borderColor: '#ffd591', color: '#d46b08' }}>
                            <InfoCircleOutlined />
                            <span>此函数为单输入模式，将只使用第一个输入节点</span>
                          </div>
                        );
                      }
                      return null;
                    }}
                  </Form.Item>

                  {/* 双输入函数但只连接了一个节点时的警告 */}
                  <Form.Item noStyle shouldUpdate={(prevValues, currentValues) => prevValues.functionName !== currentValues.functionName}>
                    {({ getFieldValue }) => {
                      const functionName = getFieldValue('functionName') || 'area_ratio';
                      const singleInputFunctions = [
                        'height_ratio_frame',
                        'width_ratio_frame',
                        'area_ratio_frame',
                        'size_absolute'
                      ];

                      if (!singleInputFunctions.includes(functionName) && upstreamNodes.length < 2) {
                        return (
                          <div className="info-box" style={{ marginTop: 12, background: '#fff7e6', borderColor: '#ffd591', color: '#d46b08' }}>
                            <InfoCircleOutlined />
                            <span>此函数需要2个输入节点，请再连接一个算法节点</span>
                          </div>
                        );
                      }
                      return null;
                    }}
                  </Form.Item>
                </>
              )}
            </div>

            <div className="form-divider" />

            {/* 计算函数与判定条件 */}
            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">计算函数与判定条件</span>
              </div>

              <Form.Item
                label="计算函数"
                name="functionName"
              >
                <Select>
                  <Option value="area_ratio">面积比（双输入）</Option>
                  <Option value="height_ratio">高度比（双输入）</Option>
                  <Option value="width_ratio">宽度比（双输入）</Option>
                  <Option value="iou_check">IOU检查（双输入）</Option>
                  <Option value="distance_check">距离检查（双输入）</Option>
                  <Option value="height_ratio_frame">高度占图片比例（单输入）</Option>
                  <Option value="width_ratio_frame">宽度占图片比例（单输入）</Option>
                  <Option value="area_ratio_frame">面积占图片比例（单输入）</Option>
                  <Option value="size_absolute">绝对尺寸检测（单输入）</Option>
                </Select>
              </Form.Item>

              <Form.Item
                label="运算符"
                name="operator"
              >
                <Select>
                  <Option value="less_than">小于</Option>
                  <Option value="greater_than">大于</Option>
                  <Option value="equal">等于</Option>
                </Select>
              </Form.Item>

              <Form.Item
                label="阈值"
                name="threshold"
                extra={
                  <Form.Item noStyle shouldUpdate={(prevValues, currentValues) => prevValues.functionName !== currentValues.functionName}>
                    {({ getFieldValue }) => {
                      const functionName = getFieldValue('functionName') || 'area_ratio';
                      const singleInputFunctions = [
                        'height_ratio_frame',
                        'width_ratio_frame',
                        'area_ratio_frame',
                        'size_absolute'
                      ];

                      if (singleInputFunctions.includes(functionName)) {
                        return '对于比例函数，值为0-1之间；对于绝对尺寸函数，值为像素值';
                      }
                      return '值为0-1之间的小数，如0.7表示70%';
                    }}
                  </Form.Item>
                }
              >
                <InputNumber
                  min={0}
                  max={1000}
                  step={0.01}
                  style={{ width: '100%' }}
                />
              </Form.Item>

              {/* 仅 size_absolute 函数显示 dimension 选择器 */}
              <Form.Item noStyle shouldUpdate={(prevValues, currentValues) => prevValues.functionName !== currentValues.functionName}>
                {({ getFieldValue }) => {
                  const functionName = getFieldValue('functionName') || 'area_ratio';

                  if (functionName === 'size_absolute') {
                    return (
                      <Form.Item
                        label="检测维度"
                        name="dimension"
                        extra="选择要检测的尺寸维度"
                      >
                        <Select>
                          <Option value="height">高度</Option>
                          <Option value="width">宽度</Option>
                          <Option value="area">面积</Option>
                        </Select>
                      </Form.Item>
                    );
                  }

                  return null;
                }}
              </Form.Item>
            </div>
          </>
        );
      
      case 'roi':
        // 获取关联的视频源 - 通过 edges 找到连接的 videoSource 节点
        const getRoiVideoSource = () => {
          // 找到连接到当前 ROI 节点的输入边
          const inputEdge = edges.find(edge => edge.target === node.id);
          if (!inputEdge) return null;

          // 找到源节点
          const sourceNode = nodes.find(n => n.id === inputEdge.source);
          if (!sourceNode) return null;

          // 检查源节点是否是视频源节点
          const sourceType = sourceNode.data?.type || sourceNode.type;
          if (sourceType === 'videoSource' || sourceType === 'source') {
            const videoSourceId = sourceNode.data?.videoSourceId;
            if (videoSourceId) {
              return videoSources.find(s => String(s.id) === String(videoSourceId)) || null;
            }
          }

          // 如果连接的不是视频源，继续递归查找
          // 这里简化处理，返回第一个可用的视频源
          return videoSources[0] || null;
        };

        const roiVideoSource = getRoiVideoSource();
        const roiRegions = node.data.roiRegions || [];

        return (
          <>
            <div className="form-divider" />

            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">ROI 区域管理</span>
              </div>

              <div className="roi-status-box">
                <div className="roi-status-info">
                  <InfoCircleOutlined />
                  <span>
                    {roiRegions.length > 0
                      ? `已配置 ${roiRegions.length} 个区域`
                      : '未配置区域'}
                  </span>
                </div>
                <Button
                  type="primary"
                  icon={<EditOutlined />}
                  onClick={() => setRoiDrawerVisible(true)}
                  disabled={!roiVideoSource}
                >
                  {roiRegions.length > 0 ? '编辑区域' : '绘制区域'}
                </Button>
              </div>

              {!roiVideoSource && (
                <div className="info-box" style={{ marginTop: 12 }}>
                  <InfoCircleOutlined />
                  <span>请确保工作流中有可用的视频源并正确连接</span>
                </div>
              )}

              {roiVideoSource && (
                <div className="info-box" style={{
                  marginTop: 12,
                  background: 'linear-gradient(to right, #f6ffed, #fcffe6)',
                  borderColor: '#d9f7be',
                  color: '#389e0d'
                }}>
                  <InfoCircleOutlined />
                  <span>视频源: {roiVideoSource.name}</span>
                </div>
              )}

              {roiRegions.length > 0 && (
                <div className="roi-regions-list">
                  <Text strong>已配置的 ROI 区域:</Text>
                  <List
                    size="small"
                    dataSource={roiRegions}
                    renderItem={(region: ROIRegion, index: number) => (
                      <List.Item
                        key={index}
                        style={{
                          padding: '8px 12px',
                          background: '#fafafa',
                          borderRadius: 4,
                          marginTop: 8,
                          border: '1px solid #d9d9d9'
                        }}
                      >
                        <div style={{ width: '100%' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                            <Text strong>{region.name}</Text>
                            <Space size={4}>
                              <Tag color={region.mode === 'pre_mask' ? 'blue' : 'green'}>
                                {region.mode === 'pre_mask' ? '前置掩码' : '后置过滤'}
                              </Tag>
                              {region.mode === 'post_filter' && (
                                <Tag>{getROIAnchorLabel(region.anchor)}</Tag>
                              )}
                            </Space>
                          </div>
                          <div style={{ fontSize: 11, color: '#8c8c8c' }}>
                            {region.polygon.length} 个顶点
                          </div>
                        </div>
                      </List.Item>
                    )}
                  />
                </div>
              )}
            </div>
          </>
        );

      case 'alert':
        return (
          <>
            <Form.Item
              label="告警级别"
              name="alertLevel"
            >
              <Select>
                <Option value="info">信息</Option>
                <Option value="warning">警告</Option>
                <Option value="error">错误</Option>
                <Option value="critical">严重</Option>
              </Select>
            </Form.Item>
            <Form.Item
              label="告警类型"
              name="alertType"
              extra="用于区分不同类型的告警"
            >
              <Input placeholder="例如: person, vehicle, fire" />
            </Form.Item>
            <Form.Item
              label="告警消息"
              name="alertMessage"
              extra="自定义告警消息前缀，会自动追加执行详情"
            >
              <Input placeholder="自定义告警消息" />
            </Form.Item>
            <Form.Item
              label="消息格式"
              name="messageFormat"
              extra="执行详情的展示格式"
            >
              <Select
                placeholder="请选择消息格式"
                onChange={(value) => console.log('🔄 Select onChange:', value)}
              >
                <Option value="detailed">详细格式（包含节点ID）</Option>
                <Option value="simple">简单格式（仅消息内容）</Option>
                <Option value="summary">汇总格式（按级别分组）</Option>
              </Select>
            </Form.Item>

            <div className="form-divider" />

            {/* 触发条件配置（窗口检测） */}
            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">触发条件配置（窗口检测）</span>
              </div>

              <Form.Item
                label="启用窗口检测"
                name="triggerConditionEnable"
                extra="是否启用窗口检测验证，禁用时所有检测都会触发告警"
                valuePropName="checked"
              >
                <Switch />
              </Form.Item>

              <Form.Item noStyle shouldUpdate={(prevValues, currentValues) => prevValues.triggerConditionEnable !== currentValues.triggerConditionEnable}>
                {({ getFieldValue }) => {
                  const enableTrigger = getFieldValue('triggerConditionEnable');

                  if (!enableTrigger) {
                    return (
                      <div className="info-box" style={{ marginTop: 12, background: '#f6ffed', borderColor: '#b7eb8f', color: '#52c41a' }}>
                        <InfoCircleOutlined />
                        <span>窗口检测已禁用，所有检测都将触发告警</span>
                      </div>
                    );
                  }

                  return (
                    <>
                      <Form.Item
                        label="时间窗口（秒）"
                        name="triggerConditionWindowSize"
                        extra="统计检测情况的时间窗口"
                      >
                        <InputNumber min={1} max={300} style={{ width: '100%' }} />
                      </Form.Item>

                      <Form.Item
                        label="检测模式"
                        name="triggerConditionMode"
                      >
                        <Select>
                          <Option value="count">检测次数 (count)</Option>
                          <Option value="ratio">检测比例 (ratio)</Option>
                          <Option value="consecutive">连续检测 (consecutive)</Option>
                        </Select>
                      </Form.Item>

                      <Form.Item noStyle shouldUpdate={(prevValues, currentValues) => prevValues.triggerConditionMode !== currentValues.triggerConditionMode}>
                        {({ getFieldValue }) => {
                          const mode = getFieldValue('triggerConditionMode') || 'ratio';

                          // 根据不同模式设置不同的标签和提示
                          let label = '检测阈值';
                          let extra = '';
                          let inputType = 'number';

                          if (mode === 'ratio') {
                            label = '检测阈值（比例）';
                            extra = '0-1之间的小数，如0.3表示30%的帧检测到目标';
                            inputType = 'ratio';
                          } else if (mode === 'count') {
                            label = '检测阈值（次数）';
                            extra = '正整数，时间窗口内最少检测次数';
                            inputType = 'count';
                          } else if (mode === 'consecutive') {
                            label = '检测阈值（次数）';
                            extra = '正整数，最少连续检测次数';
                            inputType = 'count';
                          }

                          return (
                            <Form.Item
                              label={label}
                              name="triggerConditionThreshold"
                              extra={extra}
                            >
                              {inputType === 'ratio' ? (
                                <InputNumber min={0} max={1} step={0.01} style={{ width: '100%' }} />
                              ) : (
                                <InputNumber min={1} max={100} step={1} style={{ width: '100%' }} />
                              )}
                            </Form.Item>
                          );
                        }}
                      </Form.Item>
                    </>
                  );
                }}
              </Form.Item>
            </div>

            <div className="form-divider" />

            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">VL 二次核验</span>
              </div>

              <Form.Item
                label="启用 VL 核验"
                name="vlValidationEnable"
                extra="开启后，告警在真正输出前会调用全局配置的 VL 模型做一次复核。"
                valuePropName="checked"
              >
                <Switch disabled={!vlConfigReady} />
              </Form.Item>

              <Form.Item noStyle shouldUpdate={(prevValues, currentValues) => prevValues.vlValidationEnable !== currentValues.vlValidationEnable}>
                {({ getFieldValue }) => {
                  const enableVlValidation = getFieldValue('vlValidationEnable');

                  if (!vlConfigReady) {
                  return (
                    <div className="info-box" style={{ marginTop: 12, background: '#fff7e6', borderColor: '#ffd591', color: '#d46b08' }}>
                      <InfoCircleOutlined />
                      <span>全局未配置 VL 模型参数，当前节点不可启用 VL 二次核验</span>
                    </div>
                  );
                }

                  if (!enableVlValidation) {
                  return (
                    <div className="info-box" style={{ marginTop: 12, background: '#f6ffed', borderColor: '#b7eb8f', color: '#52c41a' }}>
                      <InfoCircleOutlined />
                      <span>VL 核验已禁用，满足条件后将直接输出告警</span>
                    </div>
                  );
                }

                return (
                    <>
                    <Form.Item
                      label="核验提示词模板"
                      name="vlValidationPromptTemplate"
                      rules={[
                        {
                          validator: (_, value) => {
                            if (!getFieldValue('vlValidationEnable')) {
                              return Promise.resolve();
                            }
                            if ((value || '').trim()) {
                              return Promise.resolve();
                            }
                            return Promise.reject(new Error('启用 VL 核验时必须配置核验提示词模板'));
                          }
                        }
                      ]}
                      extra="支持占位符：{alert_type}、{alert_message}、{detection_count}、{detection_summary}、{detections_json}、{workflow_name}、{workflow_id}、{node_id}"
                    >
                      <Input.TextArea
                        rows={8}
                        placeholder={'例如：请根据图像判断是否真的发生“{alert_type}”告警。\n当前告警文案：{alert_message}\n检测数量：{detection_count}\n检测摘要：\n{detection_summary}\n\n只返回JSON：{"is_alert": true/false, "confidence": 0-1, "reason": "..."}'}
                      />
                    </Form.Item>

                    <div className="info-box" style={{ marginTop: 12, background: '#fff7e6', borderColor: '#ffd591', color: '#d46b08' }}>
                      <InfoCircleOutlined />
                      <span>每个告警输出节点都应单独配置核验提示词模板，用于表达当前算法的判定规则</span>
                    </div>
                    </>
                  );
                }}
              </Form.Item>
            </div>

            <div className="form-divider" />

            {/* 告警抑制配置（触发后冷却期） */}
            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">告警抑制配置（触发后冷却期）</span>
              </div>

              <Form.Item
                label="启用告警抑制"
                name="suppressionEnable"
                extra="是否启用告警抑制，启用后触发告警的 N 秒内不会再触发"
                valuePropName="checked"
              >
                <Switch />
              </Form.Item>

              <Form.Item noStyle shouldUpdate={(prevValues, currentValues) => prevValues.suppressionEnable !== currentValues.suppressionEnable}>
                {({ getFieldValue }) => {
                  const enableSuppression = getFieldValue('suppressionEnable');

                  if (!enableSuppression) {
                    return (
                      <div className="info-box" style={{ marginTop: 12, background: '#f6ffed', borderColor: '#b7eb8f', color: '#52c41a' }}>
                        <InfoCircleOutlined />
                        <span>告警抑制已禁用，满足触发条件时每次都会告警</span>
                      </div>
                    );
                  }

                  return (
                    <Form.Item
                      label="抑制时长（秒）"
                      name="suppressionSeconds"
                      extra="触发告警后，N秒内不会再触发告警"
                    >
                      <InputNumber min={1} max={3600} step={1} style={{ width: '100%' }} />
                    </Form.Item>
                  );
                }}
              </Form.Item>
            </div>

            {/* 消息投递输出 */}
            <div className="config-section">
              <div className="config-section-header">
                <span className="config-section-title">消息投递输出</span>
              </div>

              <Form.Item
                label="输出到消息通道"
                name="publishToMq"
                extra="开启后，该节点告警在真正触发（通过触发条件、抑制期、VL 核验）后会异步投递到系统设置中选择的 MQTT、RabbitMQ 或 HTTP API。关闭后告警记录与录像不受影响。"
                valuePropName="checked"
              >
                <Switch />
              </Form.Item>
            </div>
          </>
        );

      case 'record':
        return (
          <>
            <Form.Item
              label="录像时长"
              name="recordDuration"
            >
              <Select>
                <Option value={5}>5 秒</Option>
                <Option value={10}>10 秒</Option>
                <Option value={30}>30 秒</Option>
                <Option value={60}>60 秒</Option>
              </Select>
            </Form.Item>
          </>
        );

      default:
        return null;
    }
  };

  return (
    <div className="property-panel">
      <div className="panel-header">
        <Space size="small">
          <SettingOutlined />
          <span className="panel-title">节点属性</span>
        </Space>
        <Button
          type="text"
          size="small"
          icon={<DeleteOutlined />}
          onClick={() => onDelete(node.id)}
          className="delete-btn"
        >
          删除
        </Button>
      </div>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        size="small"
        className="property-tabs"
      >
        <Tabs.TabPane tab="基本属性" key="basic">
          <Form
            key={node.id}
            form={form}
            layout="vertical"
            className="property-form"
            onValuesChange={handleValuesChange}
          >
            <Form.Item
              label="节点名称"
              name="label"
              rules={[{ required: true, message: '请输入节点名称' }]}
            >
              <Input size="small" />
            </Form.Item>

            <Form.Item
              label="描述"
              name="description"
            >
              <TextArea rows={3} size="small" />
            </Form.Item>

            <div className="form-divider" />

            {getNodeConfigFields()}

            <div className="auto-update-hint" role="status">
              <CheckCircleOutlined />
              <span>修改会自动应用到画布，点击顶部“保存”后生效</span>
            </div>
          </Form>
        </Tabs.TabPane>

        <Tabs.TabPane tab="节点信息" key="info">
          <div className="node-info">
            <div className="info-row">
              <span className="info-label">节点 ID:</span>
              <span className="info-value">{node.id}</span>
            </div>
            <div className="info-row">
              <span className="info-label">节点类型:</span>
              <Tag color={node.data.color}>{node.data.label}</Tag>
            </div>
            <div className="info-row">
              <span className="info-label">位置:</span>
              <span className="info-value">
                X: {Math.round(node.position.x)}, Y: {Math.round(node.position.y)}
              </span>
            </div>
          </div>
        </Tabs.TabPane>
      </Tabs>

      <VideoSourceSelector
        visible={selectorVisible}
        value={node.data.dataId ?? node.data.videoSourceId}
        videoSources={videoSources}
        onChange={(value) => {
          console.log('🎬 VideoSourceSelector onChange 被调用，新值:', value);

          // 查找选中的视频源
          const selectedSource = videoSources.find(s => String(s.id) === String(value));
          if (!selectedSource) {
            console.warn('⚠️ 未找到选中的视频源，value:', value);
            setSelectorVisible(false);
            return;
          }

          // 获取当前表单的所有值
          const currentValues = form.getFieldsValue();
          console.log('📝 当前表单值（更新前）:', currentValues);

          // 合并所有数据，保留其他字段
          const updatedData = {
            label: currentValues.label || node.data.label,
            description: currentValues.description || node.data.description || '',
            dataId: selectedSource.id,  // 重要：也要更新 dataId
            videoSourceId: value,
            videoSourceName: selectedSource.name,
            videoSourceCode: selectedSource.source_code,
          };

          console.log('🔄 准备更新节点数据:', updatedData);
          console.log('🎯 选中的视频源:', selectedSource);

          // 立即更新表单值，确保表单中有最新的videoSourceId
          form.setFieldsValue({
            label: updatedData.label,
            description: updatedData.description,
            videoSourceId: value
          });

          console.log('✅ 表单值已更新');
          console.log('📝 更新后的表单值:', form.getFieldsValue());

          // 调用onUpdate更新节点数据
          console.log('📤 准备调用 onUpdate，参数:', updatedData);
          updateVersionRef.current += 1;
          onUpdate(node.id, updatedData);
          console.log('✅ 已调用onUpdate');

          setSelectorVisible(false);
        }}
        onCancel={() => setSelectorVisible(false)}
      />

      <ROIDrawer
        visible={roiDrawerVisible}
        videoSourceId={(() => {
          // 获取关联的视频源 ID
          const inputEdge = edges.find(edge => edge.target === node.id);
          if (!inputEdge) return null;

          const sourceNode = nodes.find(n => n.id === inputEdge.source);
          if (!sourceNode) return null;

          const sourceType = sourceNode.data?.type || sourceNode.type;
          if (sourceType === 'videoSource' || sourceType === 'source') {
            return sourceNode.data?.videoSourceId || null;
          }

          return null;
        })()}
        videoSourceName={(() => {
          // 获取关联的视频源名称
          const inputEdge = edges.find(edge => edge.target === node.id);
          if (!inputEdge) return undefined;

          const sourceNode = nodes.find(n => n.id === inputEdge.source);
          if (!sourceNode) return undefined;

          const sourceType = sourceNode.data?.type || sourceNode.type;
          if (sourceType === 'videoSource' || sourceType === 'source') {
            return sourceNode.data?.videoSourceName;
          }

          return undefined;
        })()}
        sourceCode={(() => {
          // 获取关联的视频源 source_code
          const inputEdge = edges.find(edge => edge.target === node.id);
          if (!inputEdge) return undefined;

          const sourceNode = nodes.find(n => n.id === inputEdge.source);
          if (!sourceNode) return undefined;

          const sourceType = sourceNode.data?.type || sourceNode.type;
          if (sourceType === 'videoSource' || sourceType === 'source') {
            return sourceNode.data?.videoSourceCode;
          }

          return undefined;
        })()}
        initialRegions={node.data.roiRegions || []}
        onClose={() => setRoiDrawerVisible(false)}
        onSave={(regions) => {
          console.log('💾 保存 ROI 区域:', regions);
          const updatedData = {
            roiRegions: regions,
          };
          updateVersionRef.current += 1;
          onUpdate(node.id, updatedData);
        }}
      />
    </div>
  );
};

export default PropertyPanel;
