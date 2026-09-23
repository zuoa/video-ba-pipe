import { tr, trf } from '@/i18n/tr';
import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Card, Col, Collapse, Descriptions, Empty, Form, Input, InputNumber,
  List, Modal, Row, Segmented, Select, Space, Switch, Tag, Upload, message,
} from 'antd';
import {
  CloudDownloadOutlined, IdcardOutlined, InboxOutlined, PlusOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import Button from '@/components/common/AppButton';
import { PageHeader, useAppConfirm } from '@/components/common';
import {
  ReIdModelBundle,
  ReIdRuntimeStatus,
  createReIdModelBundle,
  deleteReIdModelBundle,
  getReIdModelBundles,
  getReIdImportJob,
  getReIdRuntime,
  importReIdModelArtifact,
  uploadReIdModelArtifact,
  validateReIdModelBundle,
} from '@/services/api';
import './index.css';

type SourceMode = 'upload' | 'huggingface';

const DEFAULT_MODEL = {
  version: 'v1.0',
  input_size: '256x128',
  embedding_dimension: 512,
  default_similarity_threshold: 0.75,
  preprocess_preset: 'imagenet',
  input_color: 'rgb',
  runtime: 'auto',
};

const RUNTIMES = [
  { value: 'onnxruntime', label: 'ONNX Runtime' },
  { value: 'onnxruntime-cuda', label: 'ONNX Runtime CUDA' },
  { value: 'tensorrt', label: 'TensorRT EP' },
  { value: 'torchscript', label: 'TorchScript' },
  { value: 'rknn', label: 'RKNNLite' },
];

const RUNTIME_OPTIONS = [
  { value: 'auto', label: tr("按文件格式自动选择") },
  ...RUNTIMES,
];

function runtimeFromFilename(filename: string): string | null {
  const extension = filename.trim().toLowerCase().split('.').pop();
  if (extension === 'onnx') return 'onnxruntime';
  if (extension === 'pt' || extension === 'pth') return 'torchscript';
  if (extension === 'rknn') return 'rknn';
  return null;
}

function selectedRuntime(filename: string, requested?: string): string | null {
  const inferred = runtimeFromFilename(filename);
  if (!inferred) return null;
  if (!requested || requested === 'auto') return inferred;
  if (inferred === 'onnxruntime' && ['onnxruntime', 'onnxruntime-cuda', 'tensorrt'].includes(requested)) return requested;
  return inferred === requested ? requested : null;
}

function preprocessFor(values: Record<string, any>) {
  const preset = values.preprocess_preset || 'imagenet';
  const mean = preset === 'unit' || preset === 'raw'
    ? [0, 0, 0] : [123.675, 116.28, 103.53];
  const std = preset === 'unit' ? [255, 255, 255]
    : preset === 'raw' ? [1, 1, 1] : [58.395, 57.12, 57.375];
  return {
    input_layout: 'nchw', input_dtype: 'float32',
    color: values.input_color || 'rgb', mean, std,
  };
}

function batchMetadata(values: Record<string, any>) {
  const batchSize = Number(values.batch_size || 1);
  return values.dynamic_batch
    ? { batch_size: batchSize, dynamic_batch: true }
    : { batch_size: batchSize, fixed_batch: true };
}

function errorText(error: any, fallback: string) {
  return error?.response?.data?.error || error?.data?.error || error?.message || fallback;
}

async function attachArtifact(
  bundleId: number,
  mode: SourceMode,
  file: File | null,
  values: Record<string, any>,
) {
  const filename = mode === 'upload' ? file?.name || '' : String(values.filename || '');
  const runtime = selectedRuntime(filename, values.runtime);
  if (!runtime) throw new Error(tr("文件格式与推理方式不匹配；支持 .onnx、TorchScript .pt/.pth、.rknn"));
  const metadata = batchMetadata(values);
  if (mode === 'upload') {
    if (!file) throw new Error(tr("请选择模型文件"));
    const data = new FormData();
    data.append('file', file);
    data.append('runtime', runtime);
    data.append('architecture', values.architecture || 'any');
    data.append('device', values.device || 'any');
    data.append('metadata', JSON.stringify(metadata));
    if (values.upload_sha256) data.append('sha256', String(values.upload_sha256));
    await uploadReIdModelArtifact(bundleId, data);
  } else {
    await importReIdModelArtifact(bundleId, {
      type: 'huggingface', repo_id: values.repo_id, filename,
      revision: values.revision, sha256: values.sha256,
      use_mirror: values.use_mirror, runtime,
      architecture: values.architecture || 'any',
      device: values.device || 'any', metadata,
    });
  }
}

interface ModelSourceFieldsProps {
  mode: SourceMode;
  onModeChange: (mode: SourceMode) => void;
  file: File | null;
  onFileChange: (file: File | null) => void;
  error: string;
}

const ModelSourceFields: React.FC<ModelSourceFieldsProps> = ({
  mode, onModeChange, file, onFileChange, error,
}) => (
  <div className="reid-source-fields">
    <Segmented
      block
      value={mode}
      options={[
        { value: 'upload', label: <><UploadOutlined /> {tr("上传文件")}</> },
        { value: 'huggingface', label: <><CloudDownloadOutlined /> {tr("Hugging Face 下载")}</> },
      ]}
      onChange={(value) => onModeChange(value as SourceMode)}
    />
    {mode === 'upload' ? (
      <div className="reid-source-fields__upload">
        <Upload.Dragger
          accept=".onnx,.pt,.pth,.rknn"
          maxCount={1}
          fileList={file ? [{ uid: 'selected-model', name: file.name, status: 'done' }] : []}
          beforeUpload={(selected) => { onFileChange(selected); return false; }}
          onRemove={() => { onFileChange(null); return true; }}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">{tr("点击选择或拖入行人模型文件")}</p>
          <p className="ant-upload-hint">{tr("支持 .onnx、TorchScript .pt/.pth、.rknn")}</p>
        </Upload.Dragger>
      </div>
    ) : (
      <div className="reid-source-fields__remote">
        <Form.Item name="repo_id" label={tr("模型仓库")} rules={[{ required: true, message: tr("请输入仓库，例如 organization/repository") }]}>
          <Input placeholder="organization/repository" />
        </Form.Item>
        <Form.Item name="filename" label={tr("模型文件路径")} rules={[{ required: true, message: tr("请输入仓库中的模型文件路径") }]}>
          <Input placeholder="models/person-reid.onnx" />
        </Form.Item>
        <Row gutter={12}>
          <Col xs={24} sm={12}>
            <Form.Item name="revision" label={tr("固定版本")} rules={[{ required: true, message: tr("请输入 commit SHA 或固定 tag") }]}>
              <Input placeholder={tr("commit SHA 或固定 tag")} />
            </Form.Item>
          </Col>
          <Col xs={24} sm={12}>
            <Form.Item name="sha256" label={tr("文件 SHA-256")} rules={[{ required: true, pattern: /^[0-9a-fA-F]{64}$/, message: tr("请输入 64 位 SHA-256") }]}>
              <Input placeholder={tr("64 位校验值")} />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="use_mirror" label={tr("使用国内镜像")} valuePropName="checked"><Switch /></Form.Item>
      </div>
    )}
    {error ? <Alert type="error" showIcon message={error} /> : null}
  </div>
);

const ReIdModelsPage: React.FC = () => {
  const [bundles, setBundles] = useState<ReIdModelBundle[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [runtime, setRuntime] = useState<ReIdRuntimeStatus | null>(null);
  const [runtimeError, setRuntimeError] = useState('');
  const [validatingId, setValidatingId] = useState<number | null>(null);
  const [validationErrors, setValidationErrors] = useState<Record<number, string>>({});
  const [validatedIds, setValidatedIds] = useState<Record<number, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [createSourceMode, setCreateSourceMode] = useState<SourceMode>('upload');
  const [createFile, setCreateFile] = useState<File | null>(null);
  const [createFileError, setCreateFileError] = useState('');
  const [artifactBundle, setArtifactBundle] = useState<ReIdModelBundle | null>(null);
  const [artifactSourceMode, setArtifactSourceMode] = useState<SourceMode>('upload');
  const [artifactFile, setArtifactFile] = useState<File | null>(null);
  const [artifactFileError, setArtifactFileError] = useState('');
  const [createForm] = Form.useForm();
  const [artifactForm] = Form.useForm();
  const confirmAction = useAppConfirm();

  const closeCreate = () => {
    setCreateOpen(false);
    setCreateFile(null);
    setCreateFileError('');
    setCreateSourceMode('upload');
    createForm.resetFields();
  };

  const openArtifact = (bundle: ReIdModelBundle) => {
    setArtifactBundle(bundle);
    setArtifactFile(null);
    setArtifactFileError('');
    setArtifactSourceMode('upload');
    artifactForm.resetFields();
  };

  const closeArtifact = () => {
    setArtifactBundle(null);
    setArtifactFile(null);
    setArtifactFileError('');
    artifactForm.resetFields();
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await getReIdModelBundles();
      setBundles(result.bundles || []);
      setLoadError('');
    } catch (error: any) {
      setLoadError(errorText(error, tr("加载 ReID 模型失败")));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const loadRuntime = useCallback(async () => {
    try {
      const result = await getReIdRuntime();
      setRuntime(result);
      setRuntimeError('');
    } catch (error: any) {
      setRuntime(null);
      setRuntimeError(errorText(error, tr("无法读取推理 Worker 运行状态")));
    }
  }, []);

  useEffect(() => { loadRuntime(); }, [loadRuntime]);

  useEffect(() => {
    const activeJobs = bundles.flatMap((bundle) => bundle.import_jobs || [])
      .filter((job) => job.status === 'pending' || job.status === 'running');
    if (!activeJobs.length) return;
    const timer = window.setTimeout(async () => {
      const results = await Promise.all(
        activeJobs.map((job) => getReIdImportJob(job.id).catch(() => null))
      );
      await load();
      if (results.some((result) => result?.job.status === 'completed')) await loadRuntime();
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [bundles, load, loadRuntime]);

  const validateBundle = async (bundle: ReIdModelBundle) => {
    setValidatingId(bundle.id);
    setValidationErrors((previous) => ({ ...previous, [bundle.id]: '' }));
    try {
      const result = await validateReIdModelBundle(bundle.id);
      message.success(trf("__VAR0__ 试运行通过：__VAR1__ · __VAR2__D", [bundle.name, result.runtime, result.embedding_dimension]));
      setValidatedIds((previous) => ({ ...previous, [bundle.id]: true }));
      void loadRuntime();
    } catch (error: any) {
      setValidatedIds((previous) => ({ ...previous, [bundle.id]: false }));
      setValidationErrors((previous) => ({
        ...previous, [bundle.id]: errorText(error, tr("试运行失败")),
      }));
    } finally {
      setValidatingId(null);
    }
  };

  const createBundle = async () => {
    let values: any;
    try {
      await createForm.validateFields();
      values = { ...DEFAULT_MODEL, ...createForm.getFieldsValue(true) };
    } catch { return; }
    if (createSourceMode === 'upload' && !createFile) {
      setCreateFileError(tr("请先选择模型文件"));
      return;
    }
    const filename = createSourceMode === 'upload' ? createFile?.name || '' : values.filename || '';
    if (!selectedRuntime(filename, values.runtime)) {
      setCreateFileError(tr("文件格式与推理方式不匹配；支持 .onnx、TorchScript .pt/.pth、.rknn"));
      return;
    }
    if (createSourceMode === 'upload' && createFile && createFile.size > 1024 * 1024 * 1024) {
      setCreateFileError(tr("模型文件不能超过 1 GB"));
      return;
    }
    setCreateFileError('');
    setSaving(true);
    let createdBundle: ReIdModelBundle | null = null;
    try {
      const result = await createReIdModelBundle({
        name: values.name,
        version: values.version || 'v1.0',
        input_size: values.input_size || '256x128',
        embedding_dimension: values.embedding_dimension || 512,
        default_similarity_threshold: values.default_similarity_threshold ?? 0.75,
        license_name: values.license_name,
        license_url: values.license_url,
        commercial_use_allowed: Boolean(values.commercial_use_allowed),
        preprocess: preprocessFor(values),
      });
      createdBundle = result.bundle;
      await attachArtifact(createdBundle.id, createSourceMode, createFile, values);
      closeCreate();
      await load();
      if (createSourceMode === 'upload') {
        message.success(tr("模型已上传，正在检查是否可用"));
        void validateBundle(createdBundle);
      } else {
        message.success(tr("下载已开始，进度会显示在模型卡片中"));
      }
    } catch (error: any) {
      if (createdBundle) {
        closeCreate();
        await load();
        message.error(trf("模型包已建立，但文件未添加成功：__VAR0__", [errorText(error, tr("请在模型卡片中重试"))]));
      } else {
        message.error(errorText(error, tr("添加模型失败")));
      }
    } finally {
      setSaving(false);
    }
  };

  const submitArtifact = async () => {
    if (!artifactBundle) return;
    let values: any;
    try { values = await artifactForm.validateFields(); } catch { return; }
    if (artifactSourceMode === 'upload' && !artifactFile) {
      setArtifactFileError(tr("请先选择模型文件"));
      return;
    }
    const filename = artifactSourceMode === 'upload' ? artifactFile?.name || '' : values.filename || '';
    if (!selectedRuntime(filename, values.runtime)) {
      setArtifactFileError(tr("文件格式与推理方式不匹配；支持 .onnx、TorchScript .pt/.pth、.rknn"));
      return;
    }
    if (artifactSourceMode === 'upload' && artifactFile && artifactFile.size > 1024 * 1024 * 1024) {
      setArtifactFileError(tr("模型文件不能超过 1 GB"));
      return;
    }
    setArtifactFileError('');
    setSaving(true);
    try {
      await attachArtifact(artifactBundle.id, artifactSourceMode, artifactFile, values);
      closeArtifact();
      setValidatedIds((previous) => ({ ...previous, [artifactBundle.id]: false }));
      await load();
      if (artifactSourceMode === 'upload') {
        message.success(tr("模型文件已上传，正在检查是否可用"));
        void validateBundle(artifactBundle);
      } else {
        message.success(tr("下载已开始，进度会显示在模型卡片中"));
      }
    } catch (error: any) {
      message.error(errorText(error, tr("添加模型文件失败")));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="reid-page">
      <PageHeader
        icon={<IdcardOutlined />}
        eyebrow="PERSON RE-IDENTIFICATION"
        title={tr("行人 ReID")}
        subtitle={tr("添加行人模型，确认能运行后，在目标追踪算法中选择它。")}
        count={bundles.length}
        countLabel={tr("个行人模型")}
        extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>{tr("添加模型")}</Button>}
      />
      <ol className="reid-page__steps" aria-label={tr("使用步骤")}>
        <li><span>1</span><div><strong>{tr("添加模型")}</strong><small>{tr("上传行人特征模型文件")}</small></div></li>
        <li><span>2</span><div><strong>{tr("检查可用")}</strong><small>{tr("系统会在当前设备上试运行")}</small></div></li>
        <li><span>3</span><div><strong>{tr("用于追踪")}</strong><small>{tr("在目标追踪算法中选择模型")}</small></div></li>
      </ol>
      {loadError ? <Alert style={{ marginBottom: 20 }} showIcon type="error"
        message={tr("加载模型包失败")} description={loadError}
        action={<Button size="small" onClick={load}>{tr("重试")}</Button>} /> : null}
      {runtimeError ? <Alert style={{ marginBottom: 20 }} showIcon type="warning"
        message={tr("无法确认 ReID 推理环境")} description={runtimeError}
        action={<Button size="small" onClick={loadRuntime}>{tr("重试")}</Button>} /> : null}
      {runtime && !runtime.capabilities.available_runtimes?.length ? (
        <Alert style={{ marginBottom: 20 }} showIcon type="warning"
          message={tr("当前设备还没有可用的 ReID 推理环境")}
          description={tr("可以先添加模型文件，随后安装对应的推理运行时再检查。")} />
      ) : null}
      {!bundles.length && !loading && !loadError ? (
        <Card className="reid-page__empty">
          <Empty description={tr("还没有行人 ReID 模型。先添加一个模型文件。")}>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>{tr("添加第一个模型")}</Button>
          </Empty>
        </Card>
      ) : null}
      <Row gutter={[16, 16]}>
        {bundles.map((bundle) => {
          const readiness = runtime?.bundles.find((item) => item.bundle_id === bundle.id);
          return (
            <Col xs={24} xl={12} key={bundle.id}>
            <Card className="reid-model-card"
              loading={loading}
              title={<Space wrap>{bundle.name}{!bundle.enabled ? <Tag>{tr("已停用")}</Tag> : null}<Tag color={validatedIds[bundle.id] ? 'green' : validationErrors[bundle.id] ? 'red' : 'default'}>
                {validatedIds[bundle.id] ? tr("检查通过") : validationErrors[bundle.id] ? tr("检查失败") : bundle.artifacts.length ? tr("待检查") : tr("待添加文件")}
              </Tag></Space>}
              extra={<Space wrap>
                <Button size="small" disabled={!bundle.artifacts.length || !bundle.enabled}
                  loading={validatingId === bundle.id}
                  onClick={() => validateBundle(bundle)}>{tr("检查模型")}</Button>
                <Button size="small" icon={<UploadOutlined />} onClick={() => openArtifact(bundle)}>
                  {bundle.artifacts.length ? tr("添加设备版本") : tr("上传模型文件")}
                </Button>
                <Button size="small" danger onClick={() => confirmAction({
                  title: tr("删除行人模型"), objectName: bundle.name,
                  description: tr("该模型的全部文件会一并删除，使用它的跟踪算法将降级运行。"),
                  onConfirm: async () => { await deleteReIdModelBundle(bundle.id); await load(); },
                })}>{tr("删除")}</Button>
              </Space>}
            >
              {readiness && !readiness.runtime && bundle.artifacts.length ? (
                <Alert style={{ marginBottom: 12 }} showIcon type="warning"
                  message={tr("当前设备还不能使用这个模型")} description={readiness.error} />
              ) : null}
              {validationErrors[bundle.id] ? <Alert style={{ marginBottom: 12 }} showIcon
                type="error" message={tr("模型检查未通过")} description={validationErrors[bundle.id]} /> : null}
              <Descriptions size="small" column={2}>
                <Descriptions.Item label={tr("模型规格")}>{bundle.input_size} · {bundle.embedding_dimension} {tr("维")}</Descriptions.Item>
                <Descriptions.Item label={tr("模型文件")}>{bundle.artifacts.length} {tr("个")}</Descriptions.Item>
              </Descriptions>
              <List
                size="small" locale={{ emptyText: tr("还没有模型文件。点击“上传模型文件”继续。") }}
                dataSource={bundle.artifacts}
                renderItem={(item) => <List.Item>
                  <Space wrap><Tag color="blue">{RUNTIMES.find((runtimeOption) => runtimeOption.value === item.runtime)?.label || item.runtime}</Tag><span>{item.filename}</span>{item.device !== 'any' ? <span>{item.device}</span> : null}</Space>
                </List.Item>}
              />
              {(bundle.import_jobs || []).map((job) => (
                <Alert key={job.id} style={{ marginTop: 8 }} showIcon
                  type={job.status === 'failed' ? 'error' : job.status === 'completed' ? 'success' : 'info'}
                  message={trf("下载任务 #__VAR0__ · __VAR1__", [job.id, job.status === 'completed' ? tr("已完成") : job.status === 'failed' ? tr("失败") : `进行中 ${job.progress}%`])}
                  description={job.error || undefined} />
              ))}
            </Card>
            </Col>
          );
        })}
      </Row>

      <Modal
        title={tr("添加行人 ReID 模型")}
        open={createOpen}
        onCancel={closeCreate}
        onOk={createBundle}
        okText={createSourceMode === 'upload' ? tr("添加并检查") : tr("开始下载")}
        cancelText={tr("取消")}
        cancelButtonProps={{ disabled: saving }}
        confirmLoading={saving}
        maskClosable={!saving}
        closable={!saving}
        destroyOnClose
        width={600}
      >
        <p className="reid-modal__intro">{tr("给模型起个名字，再添加行人 ReID 特征模型文件。普通目标检测模型不能用于这里；模型有特殊参数时再展开高级设置。")}</p>
        <Form form={createForm} layout="vertical" initialValues={DEFAULT_MODEL}>
          <Form.Item name="name" label={tr("模型名称")} rules={[{ required: true, message: tr("请输入模型名称") }]}>
            <Input placeholder={tr("例如：门口行人模型")} maxLength={80} />
          </Form.Item>
          <ModelSourceFields
            mode={createSourceMode}
            onModeChange={(mode) => { setCreateSourceMode(mode); setCreateFile(null); setCreateFileError(''); createForm.setFieldValue('sha256', undefined); }}
            file={createFile}
            onFileChange={(selected) => { setCreateFile(selected); setCreateFileError(''); }}
            error={createFileError}
          />
          <Collapse className="reid-modal__advanced" ghost items={[{
            key: 'settings', label: tr("模型规格与授权（高级设置）"), forceRender: true,
            children: <>
              <Row gutter={12}>
                <Col xs={24} sm={12}>
                  <Form.Item name="input_size" label={tr("输入尺寸（高×宽）")} rules={[{ pattern: /^\d+[xX]\d+$/, message: tr("例如 256x128") }]}>
                    <Input placeholder="256x128" />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={12}>
                  <Form.Item name="embedding_dimension" label={tr("输出维度")}>
                    <InputNumber min={32} max={4096} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={12}>
                <Col xs={24} sm={12}>
                  <Form.Item name="preprocess_preset" label={tr("输入归一化")}>
                    <Select options={[
                      { value: 'imagenet', label: tr("常见 ImageNet 标准化") },
                      { value: 'unit', label: tr("像素缩放到 0–1") },
                      { value: 'raw', label: tr("使用原始像素值") },
                    ]} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={12}>
                  <Form.Item name="input_color" label={tr("输入颜色顺序")}>
                    <Select options={[{ value: 'rgb', label: 'RGB' }, { value: 'bgr', label: 'BGR' }]} />
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={12}>
                <Col xs={24} sm={12}>
                  <Form.Item name="default_similarity_threshold" label={tr("相似度阈值")}>
                    <InputNumber min={0} max={1} step={0.01} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={12}>
                  <Form.Item name="runtime" label={tr("推理方式")}>
                    <Select options={RUNTIME_OPTIONS} />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item name="version" label={tr("模型版本")}><Input placeholder="v1.0" /></Form.Item>
              <Row gutter={12}>
                <Col xs={24} sm={12}><Form.Item name="license_name" label={tr("许可证名称")}><Input /></Form.Item></Col>
                <Col xs={24} sm={12}><Form.Item name="license_url" label={tr("许可证链接")} rules={[{ type: 'url', message: tr("请输入完整链接") }]}><Input /></Form.Item></Col>
              </Row>
              <Form.Item name="commercial_use_allowed" label={tr("已确认允许商用")} valuePropName="checked"><Switch /></Form.Item>
            </>,
          }]} />
        </Form>
      </Modal>

      <Modal
        title={trf("为“__VAR0__”添加模型文件", [artifactBundle?.name || ''])}
        open={!!artifactBundle}
        onCancel={closeArtifact}
        onOk={submitArtifact}
        okText={artifactSourceMode === 'upload' ? tr("上传并检查") : tr("开始下载")}
        cancelText={tr("取消")}
        cancelButtonProps={{ disabled: saving }}
        confirmLoading={saving}
        maskClosable={!saving}
        closable={!saving}
        destroyOnClose
        width={600}
      >
        <p className="reid-modal__intro">{tr("如果是另一台设备使用的版本，请确保它由同一个源模型导出，且输入尺寸和输出维度相同。")}</p>
        <Form form={artifactForm} layout="vertical" initialValues={{ runtime: 'auto', architecture: 'any', device: 'any', batch_size: 1, dynamic_batch: false }}>
          <ModelSourceFields
            mode={artifactSourceMode}
            onModeChange={(mode) => { setArtifactSourceMode(mode); setArtifactFile(null); setArtifactFileError(''); artifactForm.setFieldsValue({ sha256: undefined, upload_sha256: undefined }); }}
            file={artifactFile}
            onFileChange={(selected) => { setArtifactFile(selected); setArtifactFileError(''); }}
            error={artifactFileError}
          />
          <Collapse className="reid-modal__advanced" ghost items={[{
            key: 'settings', label: tr("设备与批处理设置（高级设置）"), forceRender: true,
            children: <>
          <Form.Item name="runtime" label={tr("推理方式")}><Select options={RUNTIME_OPTIONS} /></Form.Item>
              <Row gutter={12}>
                <Col xs={24} sm={12}><Form.Item name="architecture" label={tr("处理器架构")}><Input placeholder="any" /></Form.Item></Col>
                <Col xs={24} sm={12}><Form.Item name="device" label={tr("设备标签")}><Input placeholder="any" /></Form.Item></Col>
              </Row>
              {artifactSourceMode === 'upload' ? <Form.Item name="upload_sha256" label={tr("文件 SHA-256（可选）")} rules={[{ pattern: /^[0-9a-fA-F]{64}$/, message: tr("请输入 64 位 SHA-256") }]}><Input /></Form.Item> : null}
              <Row gutter={12}>
                <Col xs={24} sm={12}><Form.Item name="batch_size" label={tr("批大小")}><InputNumber min={1} max={256} style={{ width: '100%' }} /></Form.Item></Col>
                <Col xs={24} sm={12}><Form.Item name="dynamic_batch" label={tr("支持动态批处理")} valuePropName="checked"><Switch /></Form.Item></Col>
              </Row>
            </>,
          }]} />
        </Form>
      </Modal>
    </div>
  );
};

export default ReIdModelsPage;
