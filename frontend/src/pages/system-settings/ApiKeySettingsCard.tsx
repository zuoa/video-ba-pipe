import { getDateLocale } from '@/i18n/tr';
import { tr, trf } from '@/i18n/tr';
import React, { useCallback, useEffect, useState } from 'react';
import { history } from '@umijs/max';
import { Alert, Form, Input, Modal, Space, Switch, Table, Tag, Typography, message } from 'antd';
import { CopyOutlined, KeyOutlined, PlusOutlined, ReadOutlined } from '@ant-design/icons';
import Button from '@/components/common/AppButton';
import {
  createApiKey,
  getApiKeys,
  ManagedApiKey,
  setApiKeyEnabled,
} from '@/services/api';
import { copyToClipboard } from '@/utils/clipboard';

const { Text } = Typography;

function formatDateTime(value?: string | null) {
  return value ? new Date(value).toLocaleString(getDateLocale()) : tr("从未使用");
}

const ApiKeySettingsCard: React.FC = () => {
  const [form] = Form.useForm<{ name: string }>();
  const [keys, setKeys] = useState<ManagedApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [generatedKey, setGeneratedKey] = useState<ManagedApiKey | null>(null);
  const [updatingId, setUpdatingId] = useState<number | null>(null);

  const loadKeys = useCallback(async () => {
    setLoading(true);
    try {
      const response = await getApiKeys();
      setKeys(response.keys || []);
    } catch (error: any) {
      message.error(trf("加载 API Key 失败: __VAR0__", [error.message || tr("未知错误")]));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadKeys();
  }, [loadKeys]);

  const handleCreate = async () => {
    try {
      const values = await form.validateFields();
      setCreating(true);
      const response = await createApiKey(values.name.trim());
      setCreateOpen(false);
      form.resetFields();
      setGeneratedKey(response.key);
      await loadKeys();
    } catch (error: any) {
      if (!error?.errorFields) {
        message.error(trf("生成 API Key 失败: __VAR0__", [error.message || error.error || tr("未知错误")]));
      }
    } finally {
      setCreating(false);
    }
  };

  const handleToggle = async (item: ManagedApiKey, enabled: boolean) => {
    setUpdatingId(item.id);
    try {
      await setApiKeyEnabled(item.id, enabled);
      setKeys((current) => current.map((key) => (
        key.id === item.id ? { ...key, enabled } : key
      )));
      message.success(enabled ? tr("API Key 已启用") : tr("API Key 已禁用"));
    } catch (error: any) {
      message.error(trf("更新失败: __VAR0__", [error.message || error.error || tr("未知错误")]));
    } finally {
      setUpdatingId(null);
    }
  };

  const handleCopy = async () => {
    if (!generatedKey?.key) return;
    const ok = await copyToClipboard(generatedKey.key);
    if (ok) {
      message.success(tr("API Key 已复制"));
    } else {
      message.error(tr("复制失败，请手动选择并复制"));
    }
  };

  return (
    <div className="api-key-settings">
      <Alert
        type="warning"
        showIcon
        className="system-settings-alert"
        message={tr("API Key 拥有全系统对外接口权限")}
        description={tr("完整 Key 仅在生成后展示一次。请妥善保存；禁用后使用该 Key 的调用会立即失效。")}
      />

      <div className="api-key-toolbar">
        <div>
          <strong>{tr("对外集成密钥")}</strong>
          <span>{tr("通过 X-API-Key 请求头访问 /openapi/v1")}</span>
        </div>
        <Space>
          <Button icon={<ReadOutlined />} onClick={() => history.push('/api-docs')}>
            {tr("API 使用说明")}
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            {tr("生成 API Key")}
          </Button>
        </Space>
      </div>

      <Table<ManagedApiKey>
        rowKey="id"
        loading={loading}
        dataSource={keys}
        pagination={false}
        locale={{ emptyText: tr("尚未生成 API Key") }}
        columns={[
          {
            title: tr("名称"),
            dataIndex: 'name',
            render: (value: string) => <Space><KeyOutlined /><strong>{value}</strong></Space>,
          },
          {
            title: tr("Key 前缀"),
            dataIndex: 'key_prefix',
            render: (value: string) => <Text code>{value}…</Text>,
          },
          {
            title: tr("状态"),
            dataIndex: 'enabled',
            render: (enabled: boolean) => (
              <Tag color={enabled ? 'green' : 'default'}>{enabled ? tr("已启用") : tr("已禁用")}</Tag>
            ),
          },
          {
            title: tr("最后使用"),
            dataIndex: 'last_used_at',
            render: formatDateTime,
          },
          {
            title: tr("创建时间"),
            dataIndex: 'created_at',
            render: formatDateTime,
          },
          {
            title: tr("启用"),
            key: 'actions',
            align: 'right',
            render: (_: unknown, item: ManagedApiKey) => (
              <Switch
                checked={item.enabled}
                loading={updatingId === item.id}
                onChange={(enabled) => void handleToggle(item, enabled)}
              />
            ),
          },
        ]}
      />

      <Modal
        title={tr("生成 API Key")}
        open={createOpen}
        confirmLoading={creating}
        okText={tr("生成")}
        cancelText={tr("取消")}
        onOk={() => void handleCreate()}
        onCancel={() => {
          setCreateOpen(false);
          form.resetFields();
        }}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            label={tr("Key 名称")}
            name="name"
            rules={[
              { required: true, whitespace: true, message: tr("请输入便于识别的 Key 名称") },
              { max: 100, message: tr("名称不能超过 100 个字符") },
            ]}
          >
            <Input autoFocus placeholder={tr("例如：园区平台生产环境")} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={tr("API Key 已生成")}
        open={Boolean(generatedKey)}
        footer={[
          <Button key="copy" type="primary" icon={<CopyOutlined />} onClick={() => void handleCopy()}>
            {tr("复制 API Key")}
          </Button>,
          <Button key="done" onClick={() => setGeneratedKey(null)}>{tr("我已保存")}</Button>,
        ]}
        closable={false}
        maskClosable={false}
      >
        <Alert
          type="warning"
          showIcon
          message={tr("关闭后将无法再次查看完整 Key")}
          className="system-settings-alert"
        />
        <Input.TextArea
          value={generatedKey?.key || ''}
          readOnly
          autoSize={{ minRows: 2, maxRows: 3 }}
          aria-label={tr("新生成的 API Key")}
        />
      </Modal>
    </div>
  );
};

export default ApiKeySettingsCard;
