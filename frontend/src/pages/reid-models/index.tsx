import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Card, Col, Descriptions, Form, Input, InputNumber, List, Modal,
  Row, Select, Space, Switch, Tag, Upload, message,
} from 'antd';
import { IdcardOutlined, PlusOutlined, UploadOutlined, CloudDownloadOutlined } from '@ant-design/icons';
import Button from '@/components/common/AppButton';
import { PageHeader, useAppConfirm } from '@/components/common';
import {
  ReIdModelBundle,
  createReIdModelBundle,
  deleteReIdModelBundle,
  getReIdModelBundles,
  importReIdModelArtifact,
  uploadReIdModelArtifact,
} from '@/services/api';

const RUNTIMES = [
  { value: 'onnxruntime', label: 'ONNX Runtime' },
  { value: 'onnxruntime-cuda', label: 'ONNX Runtime CUDA' },
  { value: 'tensorrt', label: 'TensorRT EP' },
  { value: 'torchscript', label: 'TorchScript' },
  { value: 'rknn', label: 'RKNNLite' },
];

const ReIdModelsPage: React.FC = () => {
  const [bundles, setBundles] = useState<ReIdModelBundle[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [artifactBundle, setArtifactBundle] = useState<ReIdModelBundle | null>(null);
  const [sourceMode, setSourceMode] = useState<'upload' | 'huggingface'>('upload');
  const [file, setFile] = useState<File | null>(null);
  const [createForm] = Form.useForm();
  const [artifactForm] = Form.useForm();
  const confirmAction = useAppConfirm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await getReIdModelBundles();
      setBundles(result.bundles || []);
    } catch (error: any) {
      message.error(`加载 ReID 模型失败：${error.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const createBundle = async () => {
    const values = await createForm.validateFields();
    await createReIdModelBundle({
      ...values,
      preprocess: {
        input_layout: 'nchw', input_dtype: 'float32', color: 'rgb',
        mean: [123.675, 116.28, 103.53], std: [58.395, 57.12, 57.375],
      },
    });
    message.success('ReID 模型包已创建');
    setCreateOpen(false);
    createForm.resetFields();
    await load();
  };

  const submitArtifact = async () => {
    if (!artifactBundle) return;
    const values = await artifactForm.validateFields();
    const batchMetadata = values.dynamic_batch
      ? { batch_size: values.batch_size || 1, dynamic_batch: true }
      : { batch_size: values.batch_size || 1, fixed_batch: true };
    if (sourceMode === 'upload') {
      if (!file) {
        message.warning('请选择模型文件');
        return;
      }
      const formData = new FormData();
      formData.append('file', file);
      formData.append('runtime', values.runtime);
      formData.append('architecture', values.architecture || 'any');
      formData.append('device', values.device || 'any');
      formData.append('metadata', JSON.stringify(batchMetadata));
      if (values.sha256) formData.append('sha256', values.sha256);
      await uploadReIdModelArtifact(artifactBundle.id, formData);
      message.success('ReID 制品上传成功');
    } else {
      await importReIdModelArtifact(artifactBundle.id, {
        type: 'huggingface', repo_id: values.repo_id, filename: values.filename,
        revision: values.revision, sha256: values.sha256,
        use_mirror: values.use_mirror, runtime: values.runtime,
        architecture: values.architecture || 'any', device: values.device || 'any',
        metadata: batchMetadata,
      });
      message.success('已创建后台下载任务，可稍后刷新查看制品');
    }
    setArtifactBundle(null);
    setFile(null);
    artifactForm.resetFields();
    await load();
  };

  return (
    <div style={{ padding: 24 }}>
      <PageHeader
        icon={<IdcardOutlined />}
        eyebrow="PERSON RE-IDENTIFICATION"
        title="行人 ReID 模型"
        subtitle="管理跨 CPU、CUDA、Jetson 与 RK3588 的同一特征空间制品"
        count={bundles.length}
        countLabel="个模型包"
        extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建模型包</Button>}
      />
      <Alert
        style={{ marginBottom: 20 }} showIcon type="info"
        message="ReID 特征只在跟踪进程内存中使用，不会写入检测结果或数据库。工作流需显式选择 botsort_reid。"
      />
      <Row gutter={[16, 16]}>
        {bundles.map((bundle) => (
          <Col xs={24} xl={12} key={bundle.id}>
            <Card
              loading={loading}
              title={<Space>{bundle.name}<Tag>{bundle.contract_id}</Tag>{bundle.enabled ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>}</Space>}
              extra={<Space>
                <Button size="small" icon={<UploadOutlined />} onClick={() => {
                  setArtifactBundle(bundle);
                  artifactForm.setFieldsValue({ runtime: 'onnxruntime', architecture: 'any', device: 'any', batch_size: 1, dynamic_batch: false });
                }}>添加制品</Button>
                <Button size="small" danger onClick={() => confirmAction({
                  title: '删除 ReID 模型包', objectName: bundle.name,
                  description: '全部平台制品会一并删除，现有 ReID 工作流将自动降级。',
                  onConfirm: async () => { await deleteReIdModelBundle(bundle.id); await load(); },
                })}>删除</Button>
              </Space>}
            >
              <Descriptions size="small" column={2}>
                <Descriptions.Item label="输入">{bundle.input_size}</Descriptions.Item>
                <Descriptions.Item label="特征">{bundle.embedding_dimension}D · cosine</Descriptions.Item>
                <Descriptions.Item label="默认阈值">{bundle.default_similarity_threshold}</Descriptions.Item>
                <Descriptions.Item label="商用声明">{bundle.commercial_use_allowed ? '允许' : '未声明'}</Descriptions.Item>
              </Descriptions>
              <List
                size="small" locale={{ emptyText: '尚未上传任何平台制品' }}
                dataSource={bundle.artifacts}
                renderItem={(item) => <List.Item>
                  <Space wrap><Tag color="blue">{item.runtime}</Tag><span>{item.architecture}/{item.device}</span><span>{item.filename}</span><Tag>{item.artifact_sha256.slice(0, 12)}</Tag></Space>
                </List.Item>}
              />
            </Card>
          </Col>
        ))}
      </Row>

      <Modal title="新建 ReID 模型包" open={createOpen} onCancel={() => setCreateOpen(false)} onOk={createBundle}>
        <Form form={createForm} layout="vertical" initialValues={{ version: 'v1.0', input_size: '256x128', embedding_dimension: 512, default_similarity_threshold: 0.75 }}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="contract_id" label="特征契约" rules={[{ required: true }]}><Input placeholder="osnet-x0.25-512-v1" /></Form.Item>
          <Row gutter={12}><Col span={12}><Form.Item name="version" label="版本"><Input /></Form.Item></Col><Col span={12}><Form.Item name="input_size" label="输入尺寸"><Input /></Form.Item></Col></Row>
          <Row gutter={12}><Col span={12}><Form.Item name="embedding_dimension" label="特征维度"><InputNumber min={32} max={4096} style={{ width: '100%' }} /></Form.Item></Col><Col span={12}><Form.Item name="default_similarity_threshold" label="默认相似度"><InputNumber min={0} max={1} step={0.01} style={{ width: '100%' }} /></Form.Item></Col></Row>
          <Form.Item name="license_name" label="许可证"><Input /></Form.Item>
          <Form.Item name="license_url" label="许可证链接"><Input /></Form.Item>
          <Form.Item name="commercial_use_allowed" label="已确认允许商用" valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>

      <Modal title={`添加平台制品 · ${artifactBundle?.name || ''}`} open={!!artifactBundle} onCancel={() => setArtifactBundle(null)} onOk={submitArtifact} width={640}>
        <Space style={{ marginBottom: 16 }}>
          <Button type={sourceMode === 'upload' ? 'primary' : 'default'} icon={<UploadOutlined />} onClick={() => setSourceMode('upload')}>上传</Button>
          <Button type={sourceMode === 'huggingface' ? 'primary' : 'default'} icon={<CloudDownloadOutlined />} onClick={() => setSourceMode('huggingface')}>Hugging Face</Button>
        </Space>
        <Form form={artifactForm} layout="vertical">
          <Row gutter={12}><Col span={8}><Form.Item name="runtime" label="运行时" rules={[{ required: true }]}><Select options={RUNTIMES} /></Form.Item></Col><Col span={8}><Form.Item name="architecture" label="架构"><Input /></Form.Item></Col><Col span={8}><Form.Item name="device" label="设备"><Input /></Form.Item></Col></Row>
          {sourceMode === 'upload' ? <Form.Item label="模型文件"><Upload beforeUpload={(selected) => { setFile(selected); return false; }} maxCount={1}><Button icon={<UploadOutlined />}>选择文件</Button></Upload></Form.Item> : <>
            <Form.Item name="repo_id" label="仓库" rules={[{ required: true }]}><Input placeholder="organization/repository" /></Form.Item>
            <Form.Item name="filename" label="仓库内文件" rules={[{ required: true }]}><Input placeholder="models/reid.onnx" /></Form.Item>
            <Form.Item name="revision" label="固定 revision" rules={[{ required: true }]}><Input placeholder="commit SHA 或不可变 tag" /></Form.Item>
            <Form.Item name="use_mirror" label="使用国内镜像" valuePropName="checked"><Switch /></Form.Item>
          </>}
          <Form.Item name="sha256" label="SHA-256" rules={sourceMode === 'huggingface' ? [{ required: true, len: 64 }] : []}><Input /></Form.Item>
          <Row gutter={12}>
            <Col span={12}><Form.Item name="batch_size" label="模型批大小"><InputNumber min={1} max={256} style={{ width: '100%' }} /></Form.Item></Col>
            <Col span={12}><Form.Item name="dynamic_batch" label="支持动态批处理" valuePropName="checked"><Switch /></Form.Item></Col>
          </Row>
        </Form>
      </Modal>
    </div>
  );
};

export default ReIdModelsPage;
