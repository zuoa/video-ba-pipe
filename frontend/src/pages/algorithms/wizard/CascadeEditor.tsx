import React, { memo, useMemo, useState } from 'react';
import {
  Alert,
  Card,
  Col,
  Collapse,
  Form,
  Image,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Tag,
  Upload,
  message,
} from 'antd';
import type { UploadFile } from 'antd/es/upload/interface';
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  DeleteOutlined,
  ExperimentOutlined,
  HolderOutlined,
  InboxOutlined,
  PlusOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import Button from '@/components/common/AppButton';
import { previewCascadeAlgorithm } from '@/services/api';

export interface CascadeStage {
  id: string;
  name: string;
  model_id: number | null;
  class_ids: number[];
  confidence: number;
  max_candidates: number;
  inference: {
    backend: string;
    nms_iou: number;
  };
  input: {
    type: 'frame' | 'parent_boxes';
    parent_stage_id?: string;
    expand_ratio?: number;
  };
}

export interface CascadeConfig {
  version: 1;
  stages: CascadeStage[];
  output: {
    label: string;
    color: string;
  };
}

interface ModelOption {
  id: number;
  name: string;
  enabled: boolean;
  model_type: string;
  framework: string;
  version?: string;
  classes?: Record<string, string>;
}

interface CascadeEditorProps {
  models: ModelOption[];
  value: CascadeConfig;
  onChange: (value: CascadeConfig) => void;
}

interface StagePreview {
  stage_id: string;
  stage_name: string;
  status: string;
  input_count: number;
  detection_count: number;
  error_count: number;
  inference_time_ms: number;
  image: string;
}

interface PreviewResult {
  success: boolean;
  error?: string;
  detection_count: number;
  result_image?: string;
  stage_previews?: StagePreview[];
}

const SUPPORTED_MODEL_TYPES = new Set(['YOLO', 'ONNX', 'RKNN']);

const createStage = (index: number, previousId?: string): CascadeStage => {
  const id = `stage_${Date.now()}_${index}`;
  return {
    id,
    name: index === 0 ? '找到主体' : '确认目标',
    model_id: null,
    class_ids: [],
    confidence: 0.6,
    max_candidates: 20,
    inference: { backend: 'auto', nms_iou: 0.45 },
    input: index === 0
      ? { type: 'frame' }
      : { type: 'parent_boxes', parent_stage_id: previousId, expand_ratio: 0.1 },
  };
};

const relinkStages = (stages: CascadeStage[]): CascadeStage[] => stages.map((stage, index) => ({
  ...stage,
  input: index === 0
    ? { type: 'frame' }
    : {
        type: 'parent_boxes',
        parent_stage_id: stages[index - 1].id,
        expand_ratio: stage.input.expand_ratio ?? 0.1,
      },
}));

const CascadeEditor: React.FC<CascadeEditorProps> = ({ models, value, onChange }) => {
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<PreviewResult | null>(null);

  const compatibleModels = useMemo(
    () => models.filter(model => model.enabled && SUPPORTED_MODEL_TYPES.has(model.model_type?.toUpperCase())),
    [models],
  );
  const modelById = useMemo(
    () => new Map(compatibleModels.map(model => [model.id, model])),
    [compatibleModels],
  );

  const setStages = (stages: CascadeStage[]) => {
    onChange({ ...value, stages: relinkStages(stages) });
    setPreview(null);
  };

  const updateStage = (index: number, patch: Partial<CascadeStage>) => {
    setStages(value.stages.map((stage, stageIndex) => (
      stageIndex === index ? { ...stage, ...patch } : stage
    )));
  };

  const moveStage = (from: number, to: number) => {
    if (to < 0 || to >= value.stages.length || from === to) return;
    const next = [...value.stages];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    setStages(next);
  };

  const addStage = () => {
    if (value.stages.length >= 8) {
      message.warning('第一版最多支持 8 个阶段');
      return;
    }
    const previous = value.stages[value.stages.length - 1];
    setStages([...value.stages, createStage(value.stages.length, previous?.id)]);
  };

  const removeStage = (index: number) => {
    if (value.stages.length <= 2) {
      message.warning('多阶段检测至少需要两个阶段');
      return;
    }
    setStages(value.stages.filter((_, stageIndex) => stageIndex !== index));
  };

  const runPreview = async () => {
    const file = fileList[0]?.originFileObj;
    if (!file) {
      message.warning('请先上传测试图片');
      return;
    }
    setPreviewing(true);
    setPreview(null);
    try {
      const result = await previewCascadeAlgorithm(value, file as File);
      setPreview(result);
      if (result.success) {
        message.success(`测试完成，形成 ${result.detection_count} 个完整阶段链`);
      } else {
        message.error(result.error || '测试失败');
      }
    } catch (error: any) {
      const detail = error?.response?.data?.error || error?.message || '测试失败';
      setPreview({ success: false, detection_count: 0, error: detail });
      message.error(detail);
    } finally {
      setPreviewing(false);
    }
  };

  return (
    <div className="cascade-editor">
      <Alert
        type="info"
        showIcon
        message="按阶段缩小判断范围"
        description="第一阶段处理完整画面；之后每个阶段只处理上一阶段命中的区域。所有阶段都命中才输出最终结果。"
      />

      <div className="cascade-rail" aria-label="检测阶段顺序">
        {value.stages.map((stage, index) => {
          const selectedModel = stage.model_id ? modelById.get(stage.model_id) : undefined;
          const classOptions = Object.entries(selectedModel?.classes || {}).map(([classId, name]) => ({
            value: Number(classId),
            label: `${name} · ${classId}`,
          }));
          return (
            <React.Fragment key={stage.id}>
              {index > 0 ? (
                <div className="cascade-connector">
                  <span>传入阶段 {index} 的目标区域</span>
                  <small>向外扩展 {Math.round((stage.input.expand_ratio || 0) * 100)}%</small>
                </div>
              ) : null}
              <Card
                className="cascade-stage-card"
                draggable
                onDragStart={() => setDragIndex(index)}
                onDragOver={event => event.preventDefault()}
                onDrop={() => {
                  if (dragIndex !== null) moveStage(dragIndex, index);
                  setDragIndex(null);
                }}
              >
                <div className="cascade-stage-header">
                  <div className="cascade-stage-title">
                    <HolderOutlined className="cascade-drag-handle" aria-hidden />
                    <span className="cascade-stage-number">{index + 1}</span>
                    <div>
                      <strong>{stage.name || `阶段 ${index + 1}`}</strong>
                      <small>{index === 0 ? '输入完整画面' : `来自 ${value.stages[index - 1].name}`}</small>
                    </div>
                  </div>
                  <Space size={4}>
                    <Button
                      size="small"
                      aria-label="上移阶段"
                      disabled={index === 0}
                      onClick={() => moveStage(index, index - 1)}
                    ><ArrowUpOutlined /></Button>
                    <Button
                      size="small"
                      aria-label="下移阶段"
                      disabled={index === value.stages.length - 1}
                      onClick={() => moveStage(index, index + 1)}
                    ><ArrowDownOutlined /></Button>
                    <Button
                      size="small"
                      tone="danger"
                      aria-label="删除阶段"
                      onClick={() => removeStage(index)}
                    ><DeleteOutlined /></Button>
                  </Space>
                </div>

                <Row gutter={16}>
                  <Col xs={24} lg={8}>
                    <Form.Item label="阶段名称" required>
                      <Input
                        value={stage.name}
                        maxLength={80}
                        placeholder={index === 0 ? '例如：找到人员' : '例如：确认烟'}
                        onChange={event => updateStage(index, { name: event.target.value })}
                      />
                    </Form.Item>
                  </Col>
                  <Col xs={24} lg={8}>
                    <Form.Item label="检测模型" required>
                      <Select
                        value={stage.model_id || undefined}
                        showSearch
                        optionFilterProp="label"
                        placeholder="选择 YOLO 兼容模型"
                        options={compatibleModels.map(model => ({
                          value: model.id,
                          label: `${model.name} · ${model.model_type}/${model.framework}`,
                        }))}
                        onChange={modelId => updateStage(index, { model_id: modelId, class_ids: [] })}
                      />
                    </Form.Item>
                  </Col>
                  <Col xs={24} sm={12} lg={5}>
                    <Form.Item label="目标类别" extra="留空表示全部类别">
                      <Select
                        mode="tags"
                        value={stage.class_ids}
                        placeholder="全部类别"
                        options={classOptions}
                        onChange={items => updateStage(index, {
                          class_ids: items
                            .map(item => Number(item))
                            .filter(item => Number.isInteger(item) && item >= 0),
                        })}
                      />
                    </Form.Item>
                  </Col>
                  <Col xs={24} sm={12} lg={3}>
                    <Form.Item label="置信度">
                      <InputNumber
                        min={0}
                        max={1}
                        step={0.05}
                        value={stage.confidence}
                        onChange={number => updateStage(index, { confidence: Number(number ?? 0.6) })}
                        style={{ width: '100%' }}
                      />
                    </Form.Item>
                  </Col>
                </Row>

                {index > 0 ? (
                  <Row gutter={16} className="cascade-transfer-settings">
                    <Col xs={24} md={12}>
                      <Form.Item
                        label="检测区域扩展比例"
                        extra="给上一阶段目标框增加上下文，0.1 表示四周各扩展 10%"
                      >
                        <InputNumber
                          min={0}
                          max={1}
                          step={0.05}
                          value={stage.input.expand_ratio}
                          onChange={number => updateStage(index, {
                            input: { ...stage.input, expand_ratio: Number(number ?? 0.1) },
                          })}
                          style={{ width: '100%' }}
                        />
                      </Form.Item>
                    </Col>
                  </Row>
                ) : null}

                <Collapse
                  ghost
                  size="small"
                  items={[{
                    key: 'advanced',
                    label: '高级推理设置',
                    children: (
                      <Row gutter={16}>
                        <Col xs={24} md={8}>
                          <Form.Item label="推理后端">
                            <Select
                              value={stage.inference.backend}
                              options={[
                                { value: 'auto', label: '自动选择' },
                                { value: 'ultralytics', label: 'Ultralytics' },
                                { value: 'onnxruntime', label: 'ONNX Runtime' },
                                { value: 'rknn', label: 'RKNNLite' },
                              ]}
                              onChange={backend => updateStage(index, {
                                inference: { ...stage.inference, backend },
                              })}
                            />
                          </Form.Item>
                        </Col>
                        <Col xs={24} md={8}>
                          <Form.Item label="NMS IOU">
                            <InputNumber
                              min={0}
                              max={1}
                              step={0.05}
                              value={stage.inference.nms_iou}
                              onChange={number => updateStage(index, {
                                inference: { ...stage.inference, nms_iou: Number(number ?? 0.45) },
                              })}
                              style={{ width: '100%' }}
                            />
                          </Form.Item>
                        </Col>
                        <Col xs={24} md={8}>
                          <Form.Item label="最大候选数" extra="限制传入下一阶段的候选，避免推理量失控">
                            <InputNumber
                              min={1}
                              max={200}
                              value={stage.max_candidates}
                              onChange={number => updateStage(index, { max_candidates: Number(number ?? 20) })}
                              style={{ width: '100%' }}
                            />
                          </Form.Item>
                        </Col>
                      </Row>
                    ),
                  }]}
                />
              </Card>
            </React.Fragment>
          );
        })}
      </div>

      <Button type="dashed" block icon={<PlusOutlined />} onClick={addStage}>
        添加下一阶段
      </Button>

      <Card title={<Space><ThunderboltOutlined />最终判定</Space>} className="cascade-output-card">
        <Row gutter={16}>
          <Col xs={24} md={16}>
            <Form.Item label="输出标签" required extra="完整阶段链命中后，在工作流和告警中使用此名称">
              <Input
                value={value.output.label}
                placeholder="例如：吸烟"
                onChange={event => onChange({
                  ...value,
                  output: { ...value.output, label: event.target.value },
                })}
              />
            </Form.Item>
          </Col>
          <Col xs={24} md={8}>
            <Form.Item label="标记颜色">
              <Input
                type="color"
                value={value.output.color}
                onChange={event => onChange({
                  ...value,
                  output: { ...value.output, color: event.target.value },
                })}
                style={{ width: 100, height: 32, padding: 2 }}
              />
            </Form.Item>
          </Col>
        </Row>
        <Alert
          type="success"
          showIcon
          message={`最终显示阶段 1“${value.stages[0]?.name || '主体'}”的框`}
          description="置信度取完整链条中最低的阶段置信度，逐阶段框会保留在检测详情中。"
        />
      </Card>

      <Card title={<Space><ExperimentOutlined />逐阶段测试</Space>} className="cascade-preview-card">
        <Upload.Dragger
          accept="image/*"
          maxCount={1}
          fileList={fileList}
          beforeUpload={() => false}
          onChange={({ fileList: next }) => {
            setFileList(next.slice(-1));
            setPreview(null);
          }}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">上传一张真实场景图片</p>
          <p className="ant-upload-hint">结果会按阶段展示候选框、裁剪区域和耗时，不会保存图片。</p>
        </Upload.Dragger>
        <Button
          type="primary"
          icon={<ExperimentOutlined />}
          loading={previewing}
          disabled={fileList.length === 0}
          onClick={runPreview}
          className="cascade-preview-button"
        >
          测试当前配置
        </Button>

        {preview?.error ? <Alert type="error" showIcon message={preview.error} /> : null}
        {preview?.stage_previews?.length ? (
          <div className="cascade-preview-results">
            {preview.stage_previews.map((stage, index) => (
              <Card key={stage.stage_id} size="small" className="cascade-preview-result">
                <div className="cascade-preview-summary">
                  <Space wrap>
                    <strong>{index + 1}. {stage.stage_name}</strong>
                    <Tag color={stage.status === 'ok' ? 'success' : 'warning'}>{stage.status}</Tag>
                    <span>输入 {stage.input_count}</span>
                    <span>命中 {stage.detection_count}</span>
                    <span>{stage.inference_time_ms} ms</span>
                  </Space>
                </div>
                <Image src={stage.image} alt={`${stage.stage_name} 测试结果`} />
              </Card>
            ))}
            {preview.result_image ? (
              <Card size="small" title={`最终结果 · ${preview.detection_count} 个完整阶段链`}>
                <Image src={preview.result_image} alt="级联检测最终结果" />
              </Card>
            ) : null}
          </div>
        ) : null}
      </Card>
    </div>
  );
};

export default memo(CascadeEditor);
