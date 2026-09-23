import { tr } from '@/i18n/tr';
import { useCallback, useEffect, useState } from 'react';
import { Form, Input, InputNumber, Select, Space, Switch, Table, Tag, message } from 'antd';
import Button from '@/components/common/AppButton';
import { ApiOutlined, PlusOutlined } from '@ant-design/icons';
import { AppModal, PageHeader, useAppConfirm } from '@/components/common';
import DetectionResponseContract from '@/components/external-api/DetectionResponseContract';
import {
  getExternalApis,
  createExternalApi,
  updateExternalApi,
  deleteExternalApi,
} from '@/services/api';
import './index.css';

const { TextArea } = Input;

const DEFAULT_INPUT_SCHEMA = [
  { name: 'workflow_id', type: 'integer', source: 'system' },
  { name: 'node_id', type: 'string', source: 'system' },
  { name: 'frame_timestamp', type: 'number', source: 'system' },
  { name: 'image_base64', type: 'string', source: 'system_optional' },
  { name: 'upstream_results', type: 'object', source: 'system_optional' },
];

const DEFAULT_OUTPUT_SCHEMA = [
  { name: 'has_detection', type: 'boolean' },
  { name: 'detections', type: 'array' },
  { name: 'metadata', type: 'object' },
];

const DEFAULT_OUTPUT_MAPPING = {
  has_detection_path: 'has_detection',
  detections_path: 'detections',
  metadata_path: 'metadata',
};

function stringifyJson(value: any, fallback: any) {
  return JSON.stringify(value ?? fallback, null, 2);
}

export default function ExternalApisPage() {
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [visible, setVisible] = useState(false);
  const [editingItem, setEditingItem] = useState<any>(null);
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm();
  const confirmAction = useAppConfirm();

  const loadItems = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getExternalApis();
      setItems(data || []);
    } catch (error) {
      message.error(tr("加载外部 API 列表失败"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadItems();
  }, [loadItems]);

  const openCreate = () => {
    setEditingItem(null);
    form.resetFields();
    form.setFieldsValue({
      method: 'POST',
      timeout_seconds: 30,
      enabled: true,
      headers: '{}',
      request_template: '{}',
      input_schema: stringifyJson(DEFAULT_INPUT_SCHEMA, DEFAULT_INPUT_SCHEMA),
      output_schema: stringifyJson(DEFAULT_OUTPUT_SCHEMA, DEFAULT_OUTPUT_SCHEMA),
      output_mapping: stringifyJson(DEFAULT_OUTPUT_MAPPING, DEFAULT_OUTPUT_MAPPING),
    });
    setVisible(true);
  };

  const openEdit = (item: any) => {
    setEditingItem(item);
    form.setFieldsValue({
      name: item.name,
      description: item.description,
      endpoint_url: item.endpoint_url,
      method: item.method,
      timeout_seconds: item.timeout_seconds,
      enabled: item.enabled,
      headers: stringifyJson(item.headers, {}),
      request_template: stringifyJson(item.request_template, {}),
      input_schema: stringifyJson(item.input_schema, DEFAULT_INPUT_SCHEMA),
      output_schema: stringifyJson(item.output_schema, DEFAULT_OUTPUT_SCHEMA),
      output_mapping: stringifyJson(item.output_mapping, DEFAULT_OUTPUT_MAPPING),
    });
    setVisible(true);
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      const values = await form.validateFields();
      const payload = {
        ...values,
        headers: JSON.parse(values.headers || '{}'),
        request_template: JSON.parse(values.request_template || '{}'),
        input_schema: JSON.parse(values.input_schema || '[]'),
        output_schema: JSON.parse(values.output_schema || '[]'),
        output_mapping: JSON.parse(values.output_mapping || '{}'),
      };

      if (editingItem) {
        await updateExternalApi(editingItem.id, payload);
        message.success(tr("外部 API 更新成功"));
      } else {
        await createExternalApi(payload);
        message.success(tr("外部 API 创建成功"));
      }
      setVisible(false);
      loadItems();
    } catch (error: any) {
      if (!error?.errorFields) {
        const jsonError = error instanceof SyntaxError ? tr("JSON 配置格式有误，请检查后重试") : null;
        message.error(jsonError || error?.message || (editingItem ? tr("更新失败") : tr("创建失败")));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = (item: any) => {
    confirmAction({
      title: tr("删除外部 API"),
      objectName: item.name,
      description: tr("删除后，引用该接口的工作流节点将无法继续调用。"),
      onConfirm: async () => {
        try {
          await deleteExternalApi(item.id);
          message.success(tr("外部 API 删除成功"));
          loadItems();
        } catch (error) {
          message.error(tr("删除失败"));
        }
      },
    });
  };

  return (
    <div className="external-apis-page">
      <PageHeader
        icon={<ApiOutlined />}
        eyebrow="EXTERNAL SERVICES"
        title={tr("外部 API")}
        subtitle={tr("集中管理第三方算法接口，供编排节点直接选择")}
        count={items.length}
        countLabel={tr("个 API")}
        extra={(
          <Button
            type="primary"
            icon={<PlusOutlined />}
            size="large"
            className="app-primary-button"
            onClick={openCreate}
          >
            {tr("新建 API")}
          </Button>
        )}
      />

      <Table
        rowKey="id"
        loading={loading}
        dataSource={items}
        className="external-apis-table"
        pagination={{ pageSize: 10 }}
        columns={[
          {
            title: tr("名称"),
            dataIndex: 'name',
            key: 'name',
            render: (_: any, record: any) => (
              <div>
                <div className="external-apis-name">{record.name}</div>
                <div className="external-apis-desc">{record.description || tr("未填写描述")}</div>
              </div>
            ),
          },
          {
            title: tr("接口地址"),
            dataIndex: 'endpoint_url',
            key: 'endpoint_url',
            render: (value: string) => <span className="external-apis-url">{value}</span>,
          },
          {
            title: tr("方法"),
            dataIndex: 'method',
            key: 'method',
            width: 100,
            render: (value: string) => <Tag color="blue">{value}</Tag>,
          },
          {
            title: tr("超时"),
            dataIndex: 'timeout_seconds',
            key: 'timeout_seconds',
            width: 100,
            render: (value: number) => `${value}s`,
          },
          {
            title: tr("状态"),
            dataIndex: 'enabled',
            key: 'enabled',
            width: 100,
            render: (value: boolean) => (
              <Tag color={value ? 'success' : 'default'}>{value ? tr("启用") : tr("停用")}</Tag>
            ),
          },
          {
            title: tr("操作"),
            key: 'actions',
            width: 160,
            render: (_: any, record: any) => (
              <Space>
                <Button size="small" onClick={() => openEdit(record)}>
                  {tr("编辑")}
                </Button>
                <Button size="small" danger onClick={() => handleDelete(record)}>
                  {tr("删除")}
                </Button>
              </Space>
            ),
          },
        ]}
      />

      <AppModal
        title={editingItem ? tr("编辑外部 API") : tr("新建外部 API")}
        description={tr("配置工作流可调用的第三方算法接口")}
        open={visible}
        onCancel={() => {
          setVisible(false);
          form.resetFields();
        }}
        onOk={handleSubmit}
        size="lg"
        okText={tr("保存配置")}
        cancelText={tr("取消")}
        confirmLoading={submitting}
        okButtonProps={{ disabled: submitting }}
        cancelButtonProps={{ disabled: submitting }}
        closable={!submitting}
        keyboard={!submitting}
        destroyOnClose
      >
        <Form form={form} layout="vertical" className="external-api-form">
          <Form.Item name="name" label={tr("名称")} rules={[{ required: true, message: tr("请输入名称") }]}>
            <Input placeholder={tr("例如：Remote YOLO Service")} />
          </Form.Item>

          <Form.Item name="description" label={tr("描述")}>
            <Input placeholder={tr("说明该接口的用途和返回格式")} />
          </Form.Item>

          <div className="external-api-form-grid">
            <Form.Item
              name="endpoint_url"
              label={tr("接口地址")}
              rules={[{ required: true, message: tr("请输入接口地址") }]}
            >
              <Input placeholder="https://api.example.com/infer" />
            </Form.Item>

            <Form.Item name="method" label={tr("请求方法")} rules={[{ required: true }]}>
              <Select
                options={[
                  { label: 'POST', value: 'POST' },
                  { label: 'PUT', value: 'PUT' },
                  { label: 'PATCH', value: 'PATCH' },
                ]}
              />
            </Form.Item>

            <Form.Item name="timeout_seconds" label={tr("默认超时（秒）")}>
              <InputNumber min={1} max={300} style={{ width: '100%' }} />
            </Form.Item>

            <Form.Item name="enabled" label={tr("启用")} valuePropName="checked">
              <Switch />
            </Form.Item>
          </div>

          <Form.Item name="headers" label={tr("请求头 JSON")}>
            <TextArea rows={4} />
          </Form.Item>

          <Form.Item name="request_template" label={tr("默认请求体 JSON")}>
            <TextArea rows={4} />
          </Form.Item>

          <Form.Item
            name="input_schema"
            label={tr("输入参数定义 JSON")}
            extra={tr("建议声明接口预期的字段，供节点配置时参考")}
          >
            <TextArea rows={5} />
          </Form.Item>

          <Form.Item
            name="output_schema"
            label={tr("输出参数定义 JSON")}
            extra={tr("建议声明接口返回结构，便于维护")}
          >
            <TextArea rows={5} />
          </Form.Item>

          <DetectionResponseContract />

          <Form.Item
            name="output_mapping"
            label={tr("输出映射 JSON")}
            extra={tr("用于把接口返回映射为工作流标准字段；嵌套返回时请确保 detections_path 指向目标数组")}
          >
            <TextArea rows={5} />
          </Form.Item>
        </Form>
      </AppModal>
    </div>
  );
}
