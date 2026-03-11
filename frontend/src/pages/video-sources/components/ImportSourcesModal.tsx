import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  message,
} from 'antd';
import {
  ApiOutlined,
  CloudDownloadOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import {
  commitImportChannels,
  discoverImportChannels,
  getSourceImportProviders,
} from '@/services/api';
import './ImportSourcesModal.css';

interface ImportSourcesModalProps {
  visible: boolean;
  onCancel: () => void;
  onImported: () => Promise<void> | void;
}

interface ProviderOption {
  type: string;
  label: string;
  description?: string;
}

interface DiscoveredChannel {
  key: string;
  channel_no: number;
  channel_name: string;
  online: boolean;
  stream: 'main' | 'sub';
  source_code: string;
  name: string;
  rtsp_url_main: string;
  rtsp_url_sub: string;
}

const DEFAULT_PROVIDER = 'hikvision_nvr';

export default function ImportSourcesModal({
  visible,
  onCancel,
  onImported,
}: ImportSourcesModalProps) {
  const [form] = Form.useForm();
  const [providers, setProviders] = useState<ProviderOption[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [importing, setImporting] = useState(false);
  const [channels, setChannels] = useState<DiscoveredChannel[]>([]);
  const [selectedKeys, setSelectedKeys] = useState<React.Key[]>([]);
  const [deviceName, setDeviceName] = useState<string>('');

  useEffect(() => {
    if (!visible) {
      form.resetFields();
      setChannels([]);
      setSelectedKeys([]);
      setDeviceName('');
      return;
    }

    form.setFieldsValue({
      provider_type: DEFAULT_PROVIDER,
      scheme: 'http',
      port: 80,
      rtsp_port: 554,
      stream_preference: 'sub',
    });

    getSourceImportProviders()
      .then((result) => setProviders(result?.providers || []))
      .catch(() => {
        setProviders([{ type: DEFAULT_PROVIDER, label: '海康 NVR' }]);
      });
  }, [visible, form]);

  const selectedChannels = useMemo(
    () => channels.filter((channel) => selectedKeys.includes(channel.key)),
    [channels, selectedKeys],
  );

  const updateChannel = (key: React.Key, patch: Partial<DiscoveredChannel>) => {
    setChannels((current) =>
      current.map((channel) => (channel.key === key ? { ...channel, ...patch } : channel)),
    );
  };

  const handleDiscover = async () => {
    try {
      const values = await form.validateFields();
      setDiscovering(true);
      const result = await discoverImportChannels(values);
      const nextChannels = (result?.channels || []).map((item: any) => ({
        key: String(item.channel_no),
        channel_no: item.channel_no,
        channel_name: item.channel_name,
        online: item.online !== false,
        stream: (values.stream_preference || item.default_stream || 'sub') as 'main' | 'sub',
        source_code: item.default_source_code,
        name: item.channel_name,
        rtsp_url_main: item.rtsp_url_main,
        rtsp_url_sub: item.rtsp_url_sub,
      }));

      setDeviceName(result?.device_name || values.host);
      setChannels(nextChannels);
      setSelectedKeys(nextChannels.filter((item: DiscoveredChannel) => item.online).map((item: DiscoveredChannel) => item.key));
      message.success(`发现 ${nextChannels.length} 个通道`);
    } catch (error: any) {
      message.error(error?.response?.data?.error || error?.message || '通道发现失败');
    } finally {
      setDiscovering(false);
    }
  };

  const handleImport = async () => {
    try {
      const values = await form.validateFields();
      if (!selectedChannels.length) {
        message.warning('请至少选择一个通道');
        return;
      }

      setImporting(true);
      const payload = {
        ...values,
        channels: selectedChannels.map((channel) => ({
          channel_no: channel.channel_no,
          channel_name: channel.channel_name,
          name: channel.name,
          source_code: channel.source_code,
          stream: channel.stream,
        })),
      };

      const result = await commitImportChannels(payload);
      const createdCount = result?.created_count || 0;
      const errorCount = result?.errors?.length || 0;

      if (createdCount > 0) {
        message.success(`成功导入 ${createdCount} 个通道`);
      }
      if (errorCount > 0) {
        message.warning(`${errorCount} 个通道导入失败`);
      }

      await onImported();
      onCancel();
    } catch (error: any) {
      message.error(error?.response?.data?.error || error?.message || '批量导入失败');
    } finally {
      setImporting(false);
    }
  };

  const columns = [
    {
      title: '通道',
      dataIndex: 'channel_no',
      width: 90,
      render: (value: number) => <span className="import-channel-no">CH {value}</span>,
    },
    {
      title: '名称',
      dataIndex: 'channel_name',
      width: 180,
      render: (_: string, record: DiscoveredChannel) => (
        <Input
          value={record.name}
          onChange={(event) => updateChannel(record.key, { name: event.target.value })}
          placeholder={record.channel_name}
        />
      ),
    },
    {
      title: '编码',
      dataIndex: 'source_code',
      width: 210,
      render: (_: string, record: DiscoveredChannel) => (
        <Input
          value={record.source_code}
          onChange={(event) => updateChannel(record.key, { source_code: event.target.value })}
          placeholder="唯一编码"
        />
      ),
    },
    {
      title: '码流',
      dataIndex: 'stream',
      width: 120,
      render: (_: string, record: DiscoveredChannel) => (
        <Select
          value={record.stream}
          options={[
            { value: 'main', label: '主码流' },
            { value: 'sub', label: '子码流' },
          ]}
          onChange={(value) => updateChannel(record.key, { stream: value })}
        />
      ),
    },
    {
      title: '状态',
      dataIndex: 'online',
      width: 100,
      render: (value: boolean) =>
        value ? <Tag color="success">在线</Tag> : <Tag color="default">离线</Tag>,
    },
  ];

  return (
    <Modal
      open={visible}
      width={1080}
      onCancel={onCancel}
      title="批量导入视频源"
      className="import-sources-modal"
      footer={
        <Space>
          <Button onClick={onCancel}>取消</Button>
          <Button icon={<ReloadOutlined />} loading={discovering} onClick={handleDiscover}>
            发现通道
          </Button>
          <Button
            type="primary"
            icon={<CloudDownloadOutlined />}
            loading={importing}
            onClick={handleImport}
          >
            导入已选通道
          </Button>
        </Space>
      }
    >
      <div className="import-modal-layout">
        <div className="import-hero">
          <div className="import-hero-icon">
            <ApiOutlined />
          </div>
          <div>
            <div className="import-hero-title">从设备 API 批量发现并导入通道</div>
            <div className="import-hero-subtitle">
              当前支持海康 NVR。后续扩展其他 NVR 或平台时复用同一入口。
            </div>
          </div>
        </div>

        <Form form={form} layout="vertical" className="import-config-form">
          <div className="import-grid">
            <Form.Item
              label="导入类型"
              name="provider_type"
              rules={[{ required: true, message: '请选择导入类型' }]}
            >
              <Select
                options={providers.map((provider) => ({
                  value: provider.type,
                  label: provider.label,
                }))}
              />
            </Form.Item>

            <Form.Item
              label="协议"
              name="scheme"
              rules={[{ required: true, message: '请选择协议' }]}
            >
              <Select
                options={[
                  { value: 'http', label: 'HTTP' },
                  { value: 'https', label: 'HTTPS' },
                ]}
              />
            </Form.Item>

            <Form.Item
              label="设备地址"
              name="host"
              rules={[{ required: true, message: '请输入设备地址' }]}
            >
              <Input placeholder="192.168.1.100" />
            </Form.Item>

            <Form.Item
              label="API 端口"
              name="port"
              rules={[{ required: true, message: '请输入 API 端口' }]}
            >
              <InputNumber min={1} max={65535} style={{ width: '100%' }} />
            </Form.Item>

            <Form.Item
              label="用户名"
              name="username"
              rules={[{ required: true, message: '请输入用户名' }]}
            >
              <Input placeholder="admin" />
            </Form.Item>

            <Form.Item
              label="密码"
              name="password"
              rules={[{ required: true, message: '请输入密码' }]}
            >
              <Input.Password placeholder="设备密码" />
            </Form.Item>

            <Form.Item
              label="RTSP 端口"
              name="rtsp_port"
              rules={[{ required: true, message: '请输入 RTSP 端口' }]}
            >
              <InputNumber min={1} max={65535} style={{ width: '100%' }} />
            </Form.Item>

            <Form.Item
              label="默认码流"
              name="stream_preference"
              rules={[{ required: true, message: '请选择默认码流' }]}
            >
              <Select
                options={[
                  { value: 'sub', label: '子码流' },
                  { value: 'main', label: '主码流' },
                ]}
              />
            </Form.Item>
          </div>
        </Form>

        {channels.length > 0 && (
          <div className="import-results">
            <div className="import-results-header">
              <div>
                <div className="import-results-title">{deviceName || '设备'} 通道列表</div>
                <div className="import-results-subtitle">
                  已发现 {channels.length} 个通道，当前选择 {selectedChannels.length} 个
                </div>
              </div>
            </div>

            <Alert
              type="info"
              showIcon
              message="每个通道会创建成一条独立视频源，保留现有手工添加模式。"
              className="import-results-alert"
            />

            <Table
              rowKey="key"
              columns={columns}
              dataSource={channels}
              pagination={false}
              size="middle"
              rowSelection={{
                selectedRowKeys: selectedKeys,
                onChange: setSelectedKeys,
                getCheckboxProps: (record: DiscoveredChannel) => ({
                  disabled: !record.online,
                }),
              }}
              scroll={{ y: 360 }}
            />
          </div>
        )}
      </div>
    </Modal>
  );
}
