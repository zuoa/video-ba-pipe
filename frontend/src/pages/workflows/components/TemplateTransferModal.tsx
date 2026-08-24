import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Checkbox,
  Descriptions,
  Divider,
  Input,
  Select,
  Space,
  Spin,
  Tag,
  Upload,
} from 'antd';
import type { UploadProps } from 'antd';
import {
  CheckCircleOutlined,
  CloudDownloadOutlined,
  InboxOutlined,
  SafetyCertificateOutlined,
  SwapOutlined,
} from '@ant-design/icons';
import AppModal from '@/components/common/AppModal';
import Button from '@/components/common/AppButton';
import {
  downloadWorkflowTemplate,
  getModels,
  getTemplateTransferCapabilities,
  importWorkflowTemplate,
  preflightWorkflowTemplate,
} from '@/services/api';
import type {
  TemplateImportPreflight,
  TemplateImportResolutions,
  TemplateTransferManifest,
  Workflow,
} from '@/services/api';
import './TemplateTransferModal.css';

type TransferMode = 'export' | 'import';

interface TemplateTransferModalProps {
  open: boolean;
  mode: TransferMode;
  template?: Workflow | null;
  onClose: () => void;
  onImported: () => void;
}

const STATUS_LABELS: Record<string, { text: string; color: string }> = {
  reuse: { text: '按标识复用', color: 'success' },
  reuse_by_hash: { text: '按内容复用', color: 'success' },
  mapped: { text: '已映射', color: 'processing' },
  import: { text: '随包导入', color: 'blue' },
  missing: { text: '需要映射', color: 'warning' },
  conflict: { text: '名称冲突', color: 'error' },
  unsupported: { text: '目标设备不支持', color: 'error' },
};

async function readManifestFromPackage(file: File): Promise<TemplateTransferManifest> {
  if (file.size < 30) throw new Error('迁移包内容不完整');
  const header = new DataView(await file.slice(0, 30).arrayBuffer());
  if (header.getUint32(0, true) !== 0x04034b50) throw new Error('不是有效的迁移 ZIP 包');
  const compression = header.getUint16(8, true);
  const compressedSize = header.getUint32(18, true);
  const filenameLength = header.getUint16(26, true);
  const extraLength = header.getUint16(28, true);
  if (compression !== 0) throw new Error('迁移包 manifest.json 必须使用未压缩格式');
  if (compressedSize <= 0 || compressedSize > 1024 * 1024) throw new Error('迁移清单大小无效');
  const nameStart = 30;
  const dataStart = nameStart + filenameLength + extraLength;
  const filename = new TextDecoder().decode(
    await file.slice(nameStart, nameStart + filenameLength).arrayBuffer(),
  );
  if (filename !== 'manifest.json') throw new Error('迁移包第一项不是 manifest.json');
  const raw = await file.slice(dataStart, dataStart + compressedSize).text();
  const manifest = JSON.parse(raw);
  if (manifest?.format !== 'video-ba-workflow-template') throw new Error('不是 Video BA 编排模板迁移包');
  return manifest;
}

const statusTag = (status: string) => {
  const item = STATUS_LABELS[status] || { text: status || '待检查', color: 'default' };
  return <Tag color={item.color}>{item.text}</Tag>;
};

export default function TemplateTransferModal({
  open,
  mode,
  template,
  onClose,
  onImported,
}: TemplateTransferModalProps) {
  const [busy, setBusy] = useState(false);
  const [includeModels, setIncludeModels] = useState(false);
  const [capabilities, setCapabilities] = useState<any>(null);
  const [file, setFile] = useState<File | null>(null);
  const [manifest, setManifest] = useState<TemplateTransferManifest | null>(null);
  const [preflight, setPreflight] = useState<TemplateImportPreflight | null>(null);
  const [resolutions, setResolutions] = useState<TemplateImportResolutions>({ secrets: {} });
  const [models, setModels] = useState<any[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    setBusy(false);
    setIncludeModels(false);
    setFile(null);
    setManifest(null);
    setPreflight(null);
    setResolutions({ secrets: {} });
    setError('');
    const load = async () => {
      try {
        const requests: Promise<any>[] = [getTemplateTransferCapabilities()];
        if (mode === 'import') requests.push(getModels({ enabled: true }));
        const [profile, modelResponse] = await Promise.all(requests);
        setCapabilities(profile);
        setModels(modelResponse?.models || []);
      } catch (requestError: any) {
        setError(requestError?.data?.error || requestError?.message || '读取设备迁移能力失败');
      }
    };
    load();
  }, [mode, open]);

  const runPreflight = useCallback(async (
    nextManifest: TemplateTransferManifest,
    nextResolutions: TemplateImportResolutions,
  ) => {
    const result = await preflightWorkflowTemplate(nextManifest, nextResolutions);
    setPreflight(result);
    return result;
  }, []);

  const initializeConflictResolutions = useCallback((result: TemplateImportPreflight) => {
    setResolutions((current) => {
      const next: TemplateImportResolutions = {
        ...current,
        models: { ...(current.models || {}) },
        algorithms: { ...(current.algorithms || {}) },
        external_apis: { ...(current.external_apis || {}) },
        hooks: { ...(current.hooks || {}) },
        secrets: { ...(current.secrets || {}) },
      };
      result.blockers.forEach((blocker) => {
        const portableId = String(blocker.portable_id || '');
        if (blocker.resource === 'template') {
          next.template = { action: 'rename', name: `${result.template.name}（导入）` };
        } else if (blocker.resource === 'model' && blocker.included && portableId) {
          next.models![portableId] = { action: 'rename', name: `${blocker.name}（导入）`, version: blocker.version };
        } else if (blocker.resource === 'algorithm' && portableId) {
          next.algorithms![portableId] = { action: 'rename', name: `${blocker.name}（导入）` };
        } else if (blocker.resource === 'external_api' && portableId) {
          next.external_apis![portableId] = { action: 'rename', name: `${blocker.name}（导入）` };
        } else if (blocker.resource === 'hook' && portableId) {
          next.hooks![portableId] = { action: 'rename', name: `${blocker.name}（导入）` };
        }
      });
      return next;
    });
  }, []);

  const handleFile = useCallback(async (nextFile: File) => {
    setBusy(true);
    setError('');
    setFile(nextFile);
    try {
      const nextManifest = await readManifestFromPackage(nextFile);
      setManifest(nextManifest);
      const result = await runPreflight(nextManifest, { secrets: {} });
      initializeConflictResolutions(result);
    } catch (nextError: any) {
      setManifest(null);
      setPreflight(null);
      setError(nextError?.data?.error || nextError?.message || '迁移包预检失败');
    } finally {
      setBusy(false);
    }
    return false;
  }, [initializeConflictResolutions, runPreflight]);

  const uploadProps: UploadProps = useMemo(() => ({
    accept: '.zip,.vbt.zip',
    maxCount: 1,
    fileList: file ? [{ uid: 'template-package', name: file.name, status: 'done' as const }] : [],
    showUploadList: true,
    beforeUpload: (selected) => {
      void handleFile(selected as File);
      return false;
    },
    onRemove: () => {
      setFile(null);
      setManifest(null);
      setPreflight(null);
      setResolutions({ secrets: {} });
    },
  }), [file, handleFile]);

  const setSecret = (key: string, value: string) => {
    setResolutions((current) => ({
      ...current,
      secrets: { ...(current.secrets || {}), [key]: value },
    }));
  };

  const setResource = (
    resource: 'models' | 'algorithms' | 'external_apis' | 'hooks',
    portableId: string,
    value: Record<string, any>,
  ) => {
    setResolutions((current) => ({
      ...current,
      [resource]: { ...(current[resource] || {}), [portableId]: value },
    }));
  };

  const handleExport = async () => {
    if (!template) return;
    setBusy(true);
    setError('');
    try {
      await downloadWorkflowTemplate(template.id, includeModels);
      onClose();
    } catch (nextError: any) {
      setError(nextError?.message || '导出失败');
    } finally {
      setBusy(false);
    }
  };

  const handleImport = async () => {
    if (!file || !manifest) return;
    setBusy(true);
    setError('');
    try {
      const checked = await runPreflight(manifest, resolutions);
      if (!checked.ready && checked.template.status !== 'already_imported') {
        setError('仍有未解决的模型映射、名称冲突或必填配置，请检查标红项目');
        return;
      }
      await importWorkflowTemplate(file, resolutions);
      onImported();
      onClose();
    } catch (nextError: any) {
      setError(nextError?.data?.error || nextError?.message || '导入失败');
    } finally {
      setBusy(false);
    }
  };

  const dependencyRows = preflight ? [
    ...preflight.dependencies.models.map((item) => ({ ...item, kind: '模型' })),
    ...preflight.dependencies.algorithms.map((item) => ({ ...item, kind: '算法' })),
    ...preflight.dependencies.external_apis.map((item) => ({ ...item, kind: '外部 API' })),
    ...preflight.dependencies.hooks.map((item) => ({ ...item, kind: 'Hook' })),
  ] : [];

  const exportBody = (
    <div className="template-transfer">
      <div className="transfer-device-rail">
        <div>
          <span>当前盒子型号</span>
          <strong>{capabilities?.device_model_code || (error ? '读取失败' : '读取中…')}</strong>
        </div>
        <SafetyCertificateOutlined />
      </div>
      {capabilities === null && !error ? (
        <div className="transfer-loading"><Spin /><span>正在读取设备信息…</span></div>
      ) : null}
      {capabilities && !capabilities.configured ? (
        <Alert type="error" showIcon message="尚未配置设备型号" description="请先在部署环境中设置 DEVICE_MODEL_CODE，再重新启动服务。" />
      ) : null}
      <Descriptions column={1} size="small" bordered>
        <Descriptions.Item label="编排模板">{template?.name}</Descriptions.Item>
        <Descriptions.Item label="运行依赖">算法、脚本、Hook、外部 API 与模型清单</Descriptions.Item>
      </Descriptions>
      <label className="transfer-model-option">
        <Checkbox checked={includeModels} onChange={(event) => setIncludeModels(event.target.checked)} />
        <span>
          <strong>携带模型文件</strong>
          <small>适合目标盒子离线或尚未部署模型的场景，迁移包可能较大。</small>
        </span>
      </label>
      {error ? <Alert type="error" showIcon message={error} /> : null}
    </div>
  );

  const importBody = (
    <div className="template-transfer">
      <Upload.Dragger {...uploadProps} disabled={busy} className="transfer-dropzone">
        <p className="ant-upload-drag-icon"><InboxOutlined /></p>
        <p className="ant-upload-text">选择编排模板迁移包</p>
        <p className="ant-upload-hint">系统会先读取清单并核对盒子型号，不会立即上传大模型文件。</p>
      </Upload.Dragger>

      {busy && !preflight ? <div className="transfer-loading"><Spin /><span>正在读取迁移清单…</span></div> : null}
      {preflight ? (
        <>
          <div className="transfer-device-rail transfer-device-rail--pair">
            <div><span>导出设备</span><strong>{preflight.source.device_model_code}</strong></div>
            <SwapOutlined />
            <div><span>当前设备</span><strong>{preflight.target.device_model_code}</strong></div>
            <CheckCircleOutlined />
          </div>
          <div className="transfer-summary">
            <div><span>模板</span><strong>{preflight.template.name}</strong></div>
            <div><span>应用版本</span><strong>{preflight.source.app_version}</strong></div>
            <div><span>依赖项</span><strong>{dependencyRows.length}</strong></div>
          </div>

          {dependencyRows.length ? (
            <div className="transfer-dependencies">
              {dependencyRows.map((item) => (
                <div key={`${item.kind}-${item.portable_id}`} className="transfer-dependency-row">
                  <span className="transfer-dependency-kind">{item.kind}</span>
                  <span className="transfer-dependency-name">{item.name}{item.version ? ` · ${item.version}` : ''}</span>
                  {statusTag(item.status)}
                </div>
              ))}
            </div>
          ) : <Alert type="info" showIcon message="此模板不依赖模型、算法或外部 API" />}

          {preflight.blockers.length ? <Divider orientation="left">解决依赖与冲突</Divider> : null}
          {preflight.blockers.map((blocker, index) => {
            const portableId = String(blocker.portable_id || '');
            if (blocker.status === 'unsupported') {
              return (
                <Alert
                  key={`blocker-${index}`}
                  type="error"
                  showIcon
                  message={`目标设备不支持“${blocker.name}”所需的推理能力`}
                  description="请先在目标设备安装对应运行时，或使用已兼容的算法资源。"
                />
              );
            }
            if (blocker.resource === 'model' && (blocker.status === 'missing' || !blocker.included)) {
              return (
                <div className="transfer-resolution" key={`blocker-${index}`}>
                  <label>为模型“{blocker.name}”选择文件与元数据一致的目标模型</label>
                  <Select
                    showSearch
                    optionFilterProp="label"
                    placeholder="选择已有模型"
                    value={resolutions.models?.[portableId]?.target_id}
                    options={models.map((model) => ({ value: model.id, label: `${model.name} · ${model.version}` }))}
                    onChange={(targetId) => setResource('models', portableId, { target_id: targetId })}
                  />
                </div>
              );
            }
            const resource = blocker.resource === 'model'
              ? 'models'
              : blocker.resource === 'algorithm'
                ? 'algorithms'
                : blocker.resource === 'external_api'
                  ? 'external_apis'
                  : blocker.resource === 'hook'
                    ? 'hooks'
                  : null;
            const currentName = resource && portableId
              ? (resolutions[resource] as Record<string, any> | undefined)?.[portableId]?.name
              : resolutions.template?.name;
            return (
              <div className="transfer-resolution" key={`blocker-${index}`}>
                <label>“{blocker.name || preflight.template.name}”已存在，指定导入名称</label>
                <Input
                  value={currentName}
                  onChange={(event) => {
                    if (resource && portableId) {
                      const existing = (resolutions[resource] as Record<string, any> | undefined)?.[portableId] || {};
                      setResource(resource, portableId, { ...existing, action: 'rename', name: event.target.value });
                    } else {
                      setResolutions((current) => ({ ...current, template: { action: 'rename', name: event.target.value } }));
                    }
                  }}
                />
              </div>
            );
          })}

          {preflight.required_inputs.length ? <Divider orientation="left">导入时必填</Divider> : null}
          {preflight.required_inputs.map((input) => (
            <div className="transfer-resolution" key={input.key}>
              <label>{input.label}</label>
              <Input.Password
                autoComplete="new-password"
                value={resolutions.secrets?.[input.key] || ''}
                onChange={(event) => setSecret(input.key, event.target.value)}
              />
            </div>
          ))}
        </>
      ) : null}
      {error ? <Alert type="error" showIcon message={error} /> : null}
    </div>
  );

  return (
    <AppModal
      open={open}
      title={mode === 'export' ? '导出编排模板' : '导入编排模板'}
      description={mode === 'export' ? '生成可在同型号盒子间迁移的依赖包' : '先核对设备型号，再解决目标环境中的依赖'}
      size="lg"
      onCancel={onClose}
      keyboard={!busy}
      maskClosable={false}
      footer={(
        <Space>
          <Button onClick={onClose} disabled={busy}>取消</Button>
          <Button
            type="primary"
            icon={mode === 'export' ? <CloudDownloadOutlined /> : <SafetyCertificateOutlined />}
            loading={busy}
            disabled={mode === 'export' ? !capabilities?.configured : !file || !manifest}
            onClick={mode === 'export' ? handleExport : handleImport}
          >
            {mode === 'export' ? '生成并下载' : '校验并导入'}
          </Button>
        </Space>
      )}
    >
      {mode === 'export' ? exportBody : importBody}
    </AppModal>
  );
}
