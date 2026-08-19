import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Steps,
  Table,
  Tag,
  message,
} from 'antd';
import Button from '@/components/common/AppButton';
import {
  CloudDownloadOutlined,
  ReloadOutlined,
  ScanOutlined,
} from '@ant-design/icons';
import {
  fetchOnvifProfiles,
  importOnvifSources,
  scanOnvifDevices,
} from '@/services/api';
import AppModal from '@/components/common/AppModal';
import './ImportSourcesModal.css';
import './OnvifScanModal.css';

interface OnvifScanModalProps {
  visible: boolean;
  onCancel: () => void;
  onImported: () => Promise<void> | void;
}

interface ScannedDevice {
  key: string;
  host: string;
  port: number;
  xaddr: string;
  name: string;
  hardware?: string;
  already_imported: boolean;
  username: string;
  password: string;
}

interface DiscoveredProfile {
  key: string;
  host: string;
  port: number;
  token: string;
  profile_name: string;
  encoding: string;
  width?: number | null;
  height?: number | null;
  stream_hint: 'main' | 'sub';
  source_code: string;
  name: string;
  source_url: string;
  already_imported: boolean;
}

const MODE_OPTIONS = [
  { value: 'multicast', label: 'LAN 组播' },
  { value: 'subnet', label: '网段探测' },
  { value: 'host', label: '指定 IP' },
];

function deviceKey(host: string, port: number) {
  return `${host}:${port}`;
}

function emptyHint(mode: string) {
  if (mode === 'multicast') {
    return '未发现 ONVIF 设备。Docker bridge 网络通常收不到组播，请改用网段探测或指定 IP。';
  }
  if (mode === 'subnet') {
    return '该网段未探测到 ONVIF 服务。可调整端口列表，或改用指定 IP。';
  }
  return '无法将该地址作为 ONVIF 设备加入候选。';
}

export default function OnvifScanModal({
  visible,
  onCancel,
  onImported,
}: OnvifScanModalProps) {
  const [form] = Form.useForm();
  const [step, setStep] = useState(0);
  const [scanning, setScanning] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [importing, setImporting] = useState(false);
  const [devices, setDevices] = useState<ScannedDevice[]>([]);
  const [selectedDeviceKeys, setSelectedDeviceKeys] = useState<React.Key[]>([]);
  const [profiles, setProfiles] = useState<DiscoveredProfile[]>([]);
  const [selectedProfileKeys, setSelectedProfileKeys] = useState<React.Key[]>([]);
  const mode = Form.useWatch('mode', form) || 'multicast';
  const busy = scanning || fetching || importing;

  useEffect(() => {
    if (!visible) {
      form.resetFields();
      setStep(0);
      setDevices([]);
      setSelectedDeviceKeys([]);
      setProfiles([]);
      setSelectedProfileKeys([]);
      return;
    }

    form.setFieldsValue({
      mode: 'multicast',
      timeout_seconds: 5,
      subnet: '192.168.1.0/24',
      ports: '80,8000,8080,8899,2020',
      host: '',
      port: 80,
      username: 'admin',
      password: '',
    });
  }, [visible, form]);

  const selectedDevices = useMemo(
    () => devices.filter((device) => selectedDeviceKeys.includes(device.key)),
    [devices, selectedDeviceKeys],
  );
  const selectedProfiles = useMemo(
    () => profiles.filter((profile) => selectedProfileKeys.includes(profile.key)),
    [profiles, selectedProfileKeys],
  );

  const updateDevice = (key: React.Key, patch: Partial<ScannedDevice>) => {
    setDevices((current) =>
      current.map((device) => (device.key === key ? { ...device, ...patch } : device)),
    );
  };

  const updateProfile = (key: React.Key, patch: Partial<DiscoveredProfile>) => {
    setProfiles((current) =>
      current.map((profile) => (profile.key === key ? { ...profile, ...patch } : profile)),
    );
  };

  const handleScan = async () => {
    try {
      const values = await form.validateFields(
        mode === 'subnet'
          ? ['mode', 'timeout_seconds', 'subnet', 'ports']
          : mode === 'host'
            ? ['mode', 'timeout_seconds', 'host', 'port']
            : ['mode', 'timeout_seconds'],
      );
      setScanning(true);
      const result = await scanOnvifDevices(values);
      const nextDevices = (result?.devices || []).map((item: any) => ({
        key: deviceKey(item.host, item.port),
        host: item.host,
        port: item.port,
        xaddr: item.xaddr,
        name: item.name || item.host,
        hardware: item.hardware,
        already_imported: !!item.already_imported,
        username: '',
        password: '',
      }));
      setDevices(nextDevices);
      setSelectedDeviceKeys(
        nextDevices
          .filter((item: ScannedDevice) => !item.already_imported)
          .map((item: ScannedDevice) => item.key),
      );
      setProfiles([]);
      setSelectedProfileKeys([]);
      setStep(0);
      if (nextDevices.length === 0) {
        message.warning(emptyHint(values.mode));
      } else {
        message.success(`发现 ${nextDevices.length} 台设备`);
      }
    } catch (error: any) {
      if (error?.errorFields) {
        return;
      }
      message.error(error?.response?.data?.error || error?.message || 'ONVIF 扫描失败');
    } finally {
      setScanning(false);
    }
  };

  const handleFetchProfiles = async () => {
    if (!selectedDevices.length) {
      message.warning('请至少选择一台设备');
      return;
    }

    try {
      const values = await form.validateFields(['username', 'password']);
      setFetching(true);
      const timeoutSeconds = Number(form.getFieldValue('timeout_seconds') || 5);
      const results = await Promise.allSettled(
        selectedDevices.map((device) =>
          fetchOnvifProfiles({
            xaddr: device.xaddr,
            username: device.username || values.username,
            password: device.password || values.password || '',
            timeout_seconds: timeoutSeconds,
          }).then((result) => ({ device, result })),
        ),
      );

      const nextProfiles: DiscoveredProfile[] = [];
      const failures: string[] = [];
      results.forEach((item, index) => {
        const device = selectedDevices[index];
        if (item.status !== 'fulfilled') {
          failures.push(`${device.host}: ${item.reason?.response?.data?.error || item.reason?.message || '拉取失败'}`);
          return;
        }
        const payload = item.value.result;
        const deviceName =
          [payload?.device?.manufacturer, payload?.device?.model].filter(Boolean).join(' ')
          || device.name
          || device.host;
        const rows = payload?.profiles || [];
        rows.forEach((profile: any) => {
          nextProfiles.push({
            key: `${device.key}:${profile.token}`,
            host: payload.host || device.host,
            port: payload.port || device.port,
            token: profile.token,
            profile_name: profile.name,
            encoding: profile.encoding || 'unknown',
            width: profile.width,
            height: profile.height,
            stream_hint: profile.stream_hint === 'main' ? 'main' : 'sub',
            source_code: profile.default_source_code,
            name: rows.length > 1 ? `${deviceName} ${profile.name}` : deviceName,
            source_url: profile.rtsp_url,
            already_imported: !!profile.already_imported,
          });
        });
      });

      setProfiles(nextProfiles);
      const preferred = new Map<string, DiscoveredProfile>();
      nextProfiles.forEach((profile) => {
        if (profile.already_imported) {
          return;
        }
        const current = preferred.get(deviceKey(profile.host, profile.port));
        if (!current || (current.stream_hint !== 'sub' && profile.stream_hint === 'sub')) {
          preferred.set(deviceKey(profile.host, profile.port), profile);
        }
      });
      setSelectedProfileKeys(Array.from(preferred.values()).map((item) => item.key));
      setStep(2);

      if (nextProfiles.length) {
        message.success(`获取到 ${nextProfiles.length} 条码流`);
      }
      if (failures.length) {
        message.warning(failures.join('；'));
      }
      if (!nextProfiles.length && !failures.length) {
        message.warning('未获取到可用码流');
      }
    } catch (error: any) {
      if (error?.errorFields) {
        return;
      }
      message.error(error?.response?.data?.error || error?.message || '获取码流失败');
    } finally {
      setFetching(false);
    }
  };

  const handleImport = async () => {
    if (!selectedProfiles.length) {
      message.warning('请至少选择一条码流');
      return;
    }
    try {
      setImporting(true);
      const result = await importOnvifSources({
        sources: selectedProfiles.map((profile) => ({
          name: profile.name,
          source_code: profile.source_code,
          source_url: profile.source_url,
          source_codec: profile.encoding,
        })),
      });
      const createdCount = result?.created_count || 0;
      const errorCount = result?.errors?.length || 0;
      if (createdCount > 0) {
        message.success(`成功导入 ${createdCount} 个视频源`);
      }
      if (errorCount > 0) {
        message.warning(`${errorCount} 条码流导入失败`);
      }
      if (createdCount > 0) {
        await onImported();
        onCancel();
      }
    } catch (error: any) {
      message.error(error?.response?.data?.error || error?.message || '导入失败');
    } finally {
      setImporting(false);
    }
  };

  const deviceColumns = [
    {
      title: '地址',
      dataIndex: 'host',
      width: 160,
      render: (_: string, record: ScannedDevice) => (
        <span>{record.host}:{record.port}</span>
      ),
    },
    {
      title: '名称',
      dataIndex: 'name',
      width: 180,
    },
    {
      title: '型号',
      dataIndex: 'hardware',
      width: 160,
      render: (value?: string) => value || '-',
    },
    ...(step === 1
      ? [
          {
            title: '用户名',
            dataIndex: 'username',
            width: 140,
            render: (_: string, record: ScannedDevice) => (
              <Input
                value={record.username}
                placeholder="用默认账号"
                onChange={(event) => updateDevice(record.key, { username: event.target.value })}
              />
            ),
          },
          {
            title: '密码',
            dataIndex: 'password',
            width: 140,
            render: (_: string, record: ScannedDevice) => (
              <Input.Password
                value={record.password}
                placeholder="用默认密码"
                onChange={(event) => updateDevice(record.key, { password: event.target.value })}
              />
            ),
          },
        ]
      : []),
    {
      title: '状态',
      dataIndex: 'already_imported',
      width: 100,
      render: (value: boolean) =>
        value ? <Tag>已添加</Tag> : <Tag color="success">可加入</Tag>,
    },
  ];

  const profileColumns = [
    {
      title: '设备',
      dataIndex: 'host',
      width: 150,
      render: (_: string, record: DiscoveredProfile) => `${record.host}:${record.port}`,
    },
    {
      title: '名称',
      dataIndex: 'name',
      width: 180,
      render: (_: string, record: DiscoveredProfile) => (
        <Input
          value={record.name}
          onChange={(event) => updateProfile(record.key, { name: event.target.value })}
        />
      ),
    },
    {
      title: '编码',
      dataIndex: 'source_code',
      width: 210,
      render: (_: string, record: DiscoveredProfile) => (
        <Input
          value={record.source_code}
          onChange={(event) => updateProfile(record.key, { source_code: event.target.value })}
        />
      ),
    },
    {
      title: '码流',
      dataIndex: 'stream_hint',
      width: 90,
      render: (value: 'main' | 'sub') => (value === 'main' ? '主码流' : '子码流'),
    },
    {
      title: '分辨率',
      dataIndex: 'width',
      width: 110,
      render: (_: number, record: DiscoveredProfile) =>
        record.width && record.height ? `${record.width}x${record.height}` : '-',
    },
    {
      title: '编码格式',
      dataIndex: 'encoding',
      width: 90,
    },
    {
      title: '状态',
      dataIndex: 'already_imported',
      width: 90,
      render: (value: boolean) =>
        value ? <Tag>已添加</Tag> : <Tag color="success">可加入</Tag>,
    },
  ];

  return (
    <AppModal
      open={visible}
      size="xl"
      onCancel={onCancel}
      title="ONVIF 扫描"
      description="扫描局域网摄像机，拉取码流后加入视频源"
      className="import-sources-modal onvif-scan-modal"
      closable={!busy}
      keyboard={!busy}
      footer={
        <Space>
          <Button onClick={onCancel} disabled={busy}>取消</Button>
          {step > 0 && (
            <Button onClick={() => setStep(step - 1)} disabled={busy}>
              上一步
            </Button>
          )}
          {step === 0 && (
            <Button
              icon={<ReloadOutlined />}
              loading={scanning}
              disabled={busy && !scanning}
              onClick={handleScan}
            >
              开始扫描
            </Button>
          )}
          {step === 1 && (
            <Button
              type="primary"
              icon={<ScanOutlined />}
              loading={fetching}
              disabled={selectedDeviceKeys.length === 0}
              onClick={handleFetchProfiles}
            >
              获取码流
            </Button>
          )}
          {step === 2 && (
            <Button
              type="primary"
              icon={<CloudDownloadOutlined />}
              loading={importing}
              disabled={selectedProfileKeys.length === 0}
              onClick={handleImport}
            >
              导入已选码流
            </Button>
          )}
          {step === 0 && (
            <Button
              type="primary"
              disabled={busy || selectedDevices.length === 0}
              onClick={() => setStep(1)}
            >
              下一步
            </Button>
          )}
        </Space>
      }
    >
      <div className="import-modal-layout">
        <div className="import-hero">
          <div className="import-hero-icon">
            <ScanOutlined />
          </div>
          <div>
            <div className="import-hero-title">扫描 ONVIF 摄像机并加入视频源</div>
            <div className="import-hero-subtitle">
              组播适合宿主机直连；Docker 部署请用网段探测或指定 IP。
            </div>
          </div>
        </div>

        <Steps
          current={step}
          className="onvif-steps"
          items={[
            { title: '扫描设备' },
            { title: '获取码流' },
            { title: '导入视频源' },
          ]}
        />

        {step === 0 && (
          <>
            <Form form={form} layout="vertical" className="import-config-form">
              <div className="import-grid">
                <Form.Item
                  label="扫描方式"
                  name="mode"
                  rules={[{ required: true, message: '请选择扫描方式' }]}
                >
                  <Select options={MODE_OPTIONS} />
                </Form.Item>
                <Form.Item
                  label="超时（秒）"
                  name="timeout_seconds"
                  rules={[{ required: true, message: '请输入超时' }]}
                >
                  <InputNumber min={1} max={15} style={{ width: '100%' }} />
                </Form.Item>
                {mode === 'subnet' && (
                  <>
                    <Form.Item
                      label="网段"
                      name="subnet"
                      rules={[{ required: true, message: '请输入 CIDR 网段' }]}
                    >
                      <Input placeholder="192.168.1.0/24" />
                    </Form.Item>
                    <Form.Item
                      label="端口"
                      name="ports"
                      rules={[{ required: true, message: '请输入端口' }]}
                    >
                      <Input placeholder="80,8000,8080,8899,2020" />
                    </Form.Item>
                  </>
                )}
                {mode === 'host' && (
                  <>
                    <Form.Item
                      label="设备地址"
                      name="host"
                      rules={[{ required: true, message: '请输入设备地址' }]}
                    >
                      <Input placeholder="192.168.1.64" />
                    </Form.Item>
                    <Form.Item
                      label="ONVIF 端口"
                      name="port"
                      rules={[{ required: true, message: '请输入端口' }]}
                    >
                      <InputNumber min={1} max={65535} style={{ width: '100%' }} />
                    </Form.Item>
                  </>
                )}
              </div>
            </Form>

            {devices.length > 0 && (
              <div className="import-results">
                <div className="import-results-header">
                  <div>
                    <div className="import-results-title">发现的设备</div>
                    <div className="import-results-subtitle">
                      共 {devices.length} 台，已选择 {selectedDevices.length} 台
                    </div>
                  </div>
                </div>
                <Table
                  rowKey="key"
                  columns={deviceColumns}
                  dataSource={devices}
                  pagination={false}
                  size="middle"
                  rowSelection={{
                    selectedRowKeys: selectedDeviceKeys,
                    onChange: setSelectedDeviceKeys,
                  }}
                  scroll={{ y: 280 }}
                />
              </div>
            )}
          </>
        )}

        {step === 1 && (
          <>
            <Form form={form} layout="vertical" className="import-config-form">
              <div className="import-grid">
                <Form.Item
                  label="默认用户名"
                  name="username"
                  rules={[{ required: true, message: '请输入用户名' }]}
                >
                  <Input placeholder="admin" />
                </Form.Item>
                <Form.Item label="默认密码" name="password">
                  <Input.Password placeholder="设备密码" />
                </Form.Item>
              </div>
            </Form>
            <Alert
              type="info"
              showIcon
              message="表格里留空的账号密码会使用上面的默认值。已添加过的设备仍可勾选，用来补子码流；已导入的码流会在下一步禁用。"
              className="import-results-alert"
            />
            <Table
              rowKey="key"
              columns={deviceColumns}
              dataSource={selectedDevices}
              pagination={false}
              size="middle"
              scroll={{ y: 280 }}
            />
          </>
        )}

        {step === 2 && (
          <div className="import-results">
            <div className="import-results-header">
              <div>
                <div className="import-results-title">可导入码流</div>
                <div className="import-results-subtitle">
                  共 {profiles.length} 条，已选择 {selectedProfiles.length} 条。默认每台设备勾选子码流。
                </div>
              </div>
            </div>
            <Table
              rowKey="key"
              columns={profileColumns}
              dataSource={profiles}
              pagination={false}
              size="middle"
              rowSelection={{
                selectedRowKeys: selectedProfileKeys,
                onChange: setSelectedProfileKeys,
                getCheckboxProps: (record: DiscoveredProfile) => ({
                  disabled: record.already_imported,
                }),
              }}
              scroll={{ y: 360 }}
            />
          </div>
        )}
      </div>
    </AppModal>
  );
}
