import { useState } from 'react';
import { Space, Table, Tag, Typography, message } from 'antd';
import Button from '@/components/common/AppButton';
import {
  ApiOutlined,
  CopyOutlined,
  DownloadOutlined,
  FileMarkdownOutlined,
  FileTextOutlined,
  KeyOutlined,
} from '@ant-design/icons';
import { PageHeader } from '@/components/common';
import { downloadOpenApiGuide, downloadOpenApiSpec } from '@/services/api';
import { copyToClipboard } from '@/utils/clipboard';
import './index.css';

const { Paragraph, Text } = Typography;

const BASE_URL_PLACEHOLDER = 'http://<服务器地址>:5002/openapi/v1';
const API_KEY_PLACEHOLDER = 'vbp_xxxxxxxxxxxxxxxx';

const METHOD_COLORS: Record<string, string> = {
  GET: 'green',
  POST: 'blue',
  PUT: 'purple',
  PATCH: 'orange',
};

interface ParamRow {
  name: string;
  type: string;
  required?: boolean;
  defaultValue?: string;
  description: string;
}

interface EndpointDoc {
  id: string;
  method: 'GET' | 'POST' | 'PUT' | 'PATCH';
  path: string;
  summary: string;
  description?: string;
  bodyParams?: ParamRow[];
  queryParams?: ParamRow[];
  curl: string;
  responseNote?: string;
  response: string;
}

const ERROR_CODE_ROWS = [
  { status: 400, code: 'invalid_request', description: '请求体不是合法 JSON 对象' },
  { status: 400, code: 'missing_required_field', description: '缺少必填字段' },
  { status: 400, code: 'invalid_field', description: '字段取值不合法' },
  { status: 400, code: 'unknown_field', description: '包含未定义的字段' },
  { status: 400, code: 'field_not_allowed', description: '字段不允许通过该接口修改' },
  { status: 400, code: 'invalid_workflow_template', description: '模板配置校验未通过' },
  { status: 400, code: 'workflow_template_not_deactivatable', description: '模板编排不能去激活' },
  { status: 401, code: 'api_key_required', description: '缺少 X-API-Key 请求头' },
  { status: 401, code: 'invalid_api_key', description: 'API Key 无效或已禁用' },
  { status: 404, code: 'video_source_not_found', description: '视频源不存在' },
  { status: 404, code: 'workflow_template_not_found', description: '编排模板不存在' },
  { status: 404, code: 'workflow_not_found', description: '编排不存在' },
  { status: 409, code: 'source_code_exists', description: '视频源编码已存在' },
];

const ENDPOINTS: EndpointDoc[] = [
  {
    id: 'create-video-source',
    method: 'POST',
    path: '/video-sources',
    summary: '添加视频源',
    bodyParams: [
      { name: 'source_code', type: 'string', required: true, description: '视频源唯一编码,仅允许字母、数字、. _ ~ -,最长 255' },
      { name: 'name', type: 'string', required: true, description: '视频源名称' },
      { name: 'source_url', type: 'string', required: true, description: '流地址(RTSP / HTTP-FLV / HLS / 本地文件)' },
      { name: 'enabled', type: 'boolean', defaultValue: 'true', description: '是否启用' },
      { name: 'source_decode_width', type: 'integer', defaultValue: '960', description: '解码宽度' },
      { name: 'source_decode_height', type: 'integer', defaultValue: '540', description: '解码高度' },
      { name: 'source_fps', type: 'integer', defaultValue: '10', description: '目标帧率' },
      { name: 'source_codec', type: 'string', defaultValue: 'unknown', description: '编码格式:unknown / h264 / h265' },
    ],
    curl: `curl -X POST ${BASE_URL_PLACEHOLDER}/video-sources \\
  -H "X-API-Key: ${API_KEY_PLACEHOLDER}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "source_code": "cam-gate-01",
    "name": "东门相机",
    "source_url": "rtsp://admin:password@192.168.1.100:554/Streaming/Channels/101",
    "source_decode_width": 960,
    "source_decode_height": 540,
    "source_fps": 10
  }'`,
    responseNote: '201 创建成功;source_code 已存在时返回 409',
    response: `{
  "success": true,
  "data": {
    "id": 12,
    "name": "东门相机",
    "enabled": true,
    "source_code": "cam-gate-01",
    "source_url": "rtsp://admin:password@192.168.1.100:554/Streaming/Channels/101",
    "source_decode_width": 960,
    "source_decode_height": 540,
    "source_fps": 10,
    "source_codec": "unknown",
    "status": "STOPPED"
  }
}`,
  },
  {
    id: 'update-video-source',
    method: 'PATCH',
    path: '/video-sources/{source_code}',
    summary: '编辑视频源',
    description: '仅允许修改下列字段;source_code 与 source_url 不可通过本接口修改。',
    bodyParams: [
      { name: 'name', type: 'string', description: '视频源名称' },
      { name: 'enabled', type: 'boolean', description: '是否启用' },
      { name: 'source_decode_width', type: 'integer', description: '解码宽度' },
      { name: 'source_decode_height', type: 'integer', description: '解码高度' },
      { name: 'source_fps', type: 'integer', description: '目标帧率' },
      { name: 'source_codec', type: 'string', description: '编码格式:unknown / h264 / h265' },
    ],
    curl: `curl -X PATCH ${BASE_URL_PLACEHOLDER}/video-sources/cam-gate-01 \\
  -H "X-API-Key: ${API_KEY_PLACEHOLDER}" \\
  -H "Content-Type: application/json" \\
  -d '{ "name": "东门相机(高清)", "source_fps": 15 }'`,
    responseNote: '200 更新成功,返回更新后的视频源对象(结构同「添加视频源」)',
    response: `{
  "success": true,
  "data": {
    "id": 12,
    "name": "东门相机(高清)",
    "enabled": true,
    "source_code": "cam-gate-01",
    "source_fps": 15,
    "status": "STOPPED"
  }
}`,
  },
  {
    id: 'update-source-url',
    method: 'PUT',
    path: '/video-sources/{source_code}/source-url',
    summary: '更新视频源地址',
    description: '运行中的视频源将异步重启解码器并读取新地址。',
    bodyParams: [
      { name: 'source_url', type: 'string', required: true, description: '新的流地址' },
    ],
    curl: `curl -X PUT ${BASE_URL_PLACEHOLDER}/video-sources/cam-gate-01/source-url \\
  -H "X-API-Key: ${API_KEY_PLACEHOLDER}" \\
  -H "Content-Type: application/json" \\
  -d '{ "source_url": "rtsp://admin:password@192.168.1.101:554/Streaming/Channels/101" }'`,
    responseNote: '200 地址未变化或源未运行;202 已更新并安排运行时重载',
    response: `{
  "success": true,
  "data": {
    "source_code": "cam-gate-01",
    "source_url": "rtsp://admin:password@192.168.1.101:554/Streaming/Channels/101",
    "changed": true,
    "reload_scheduled": true
  }
}`,
  },
  {
    id: 'list-workflow-templates',
    method: 'GET',
    path: '/workflow-templates',
    summary: '查询全部编排模板',
    description: '返回项中的 id 即为激活接口所需的 template_workflow_id。',
    curl: `curl -H "X-API-Key: ${API_KEY_PLACEHOLDER}" \\
  ${BASE_URL_PLACEHOLDER}/workflow-templates`,
    responseNote: '200 模板列表',
    response: `{
  "success": true,
  "data": {
    "items": [
      {
        "id": 3,
        "name": "人员检测模板",
        "description": "标准人员检测编排",
        "workflow_data": { "nodes": [], "connections": [] },
        "is_active": false,
        "is_template": true,
        "config_version": 1,
        "created_at": "2026-01-01T10:00:00",
        "updated_at": "2026-01-02T10:00:00"
      }
    ],
    "total": 1
  }
}`,
  },
  {
    id: 'activate-workflow',
    method: 'POST',
    path: '/workflow-activations',
    summary: '激活编排(按模板复制)',
    description: '按「视频源 + 模板」复制生成派生编排并激活。相同视频源和模板的重复请求会复用并激活已有派生编排(幂等)。',
    bodyParams: [
      { name: 'source_code', type: 'string', required: true, description: '视频源编码' },
      { name: 'template_workflow_id', type: 'integer', required: true, description: '编排模板 ID(由「查询编排模板」获取)' },
    ],
    curl: `curl -X POST ${BASE_URL_PLACEHOLDER}/workflow-activations \\
  -H "X-API-Key: ${API_KEY_PLACEHOLDER}" \\
  -H "Content-Type: application/json" \\
  -d '{ "source_code": "cam-gate-01", "template_workflow_id": 3 }'`,
    responseNote: '201 派生编排已创建并激活;200 已有派生编排被复用并激活',
    response: `{
  "success": true,
  "data": {
    "workflow_id": 25,
    "template_workflow_id": 3,
    "source_code": "cam-gate-01",
    "created": true,
    "is_active": true
  }
}`,
  },
  {
    id: 'list-workflows',
    method: 'GET',
    path: '/workflows',
    summary: '查询派生编排',
    queryParams: [
      { name: 'source_code', type: 'string', description: '按视频源编码过滤;不传时返回全部非模板编排' },
    ],
    curl: `curl -H "X-API-Key: ${API_KEY_PLACEHOLDER}" \\
  "${BASE_URL_PLACEHOLDER}/workflows?source_code=cam-gate-01"`,
    responseNote: '200 编排列表,结构同「查询编排模板」',
    response: `{
  "success": true,
  "data": {
    "items": [
      {
        "id": 25,
        "name": "东门相机-人员检测模板",
        "is_active": true,
        "is_template": false,
        "source_template_id": 3,
        "source_template_name": "人员检测模板",
        "video_source_id": 12,
        "source_code": "cam-gate-01",
        "config_version": 1,
        "created_at": "2026-01-01T10:00:00",
        "updated_at": "2026-01-02T10:00:00"
      }
    ],
    "total": 1
  }
}`,
  },
  {
    id: 'deactivate-workflow',
    method: 'POST',
    path: '/workflows/{workflow_id}/deactivate',
    summary: '去激活编排',
    description: '模板编排不能去激活,将返回 400 workflow_template_not_deactivatable。',
    curl: `curl -X POST ${BASE_URL_PLACEHOLDER}/workflows/25/deactivate \\
  -H "X-API-Key: ${API_KEY_PLACEHOLDER}"`,
    responseNote: '200 编排已处于未激活状态',
    response: `{
  "success": true,
  "data": { "workflow_id": 25, "is_active": false }
}`,
  },
];

const paramColumns = [
  {
    title: '字段',
    dataIndex: 'name',
    width: 220,
    render: (value: string, record: ParamRow) => (
      <Space size={6}>
        <Text code>{value}</Text>
        {record.required ? <Tag color="red">必填</Tag> : null}
      </Space>
    ),
  },
  { title: '类型', dataIndex: 'type', width: 100 },
  {
    title: '默认值',
    dataIndex: 'defaultValue',
    width: 110,
    render: (value?: string) => value ?? '—',
  },
  { title: '说明', dataIndex: 'description' },
];

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

export default function ApiDocsPage() {
  const [downloading, setDownloading] = useState<'spec' | 'guide' | null>(null);

  const handleDownload = async (kind: 'spec' | 'guide') => {
    setDownloading(kind);
    try {
      if (kind === 'spec') {
        const blob = await downloadOpenApiSpec();
        saveBlob(blob, 'video-ba-pipe-openapi.yaml');
      } else {
        const blob = await downloadOpenApiGuide();
        saveBlob(blob, 'video-ba-pipe-api-usage.md');
      }
    } catch (error: any) {
      message.error(`下载失败: ${error?.message || '未知错误'}`);
    } finally {
      setDownloading(null);
    }
  };

  const handleCopy = async (text: string) => {
    const ok = await copyToClipboard(text);
    if (ok) {
      message.success('已复制');
    } else {
      message.error('复制失败,请手动选择复制');
    }
  };

  return (
    <div className="api-docs-page">
      <PageHeader
        icon={<ApiOutlined />}
        title="API 使用说明"
        subtitle="通过 X-API-Key 访问 /openapi/v1 开放接口,实现视频源与算法编排的自动化集成"
        count={ENDPOINTS.length}
        countLabel="个接口"
        extra={(
          <>
            <Button
              icon={<FileTextOutlined />}
              size="large"
              loading={downloading === 'spec'}
              onClick={() => void handleDownload('spec')}
            >
              下载 OpenAPI 规范
            </Button>
            <Button
              type="primary"
              icon={<FileMarkdownOutlined />}
              size="large"
              className="app-primary-button"
              loading={downloading === 'guide'}
              onClick={() => void handleDownload('guide')}
            >
              下载使用说明
            </Button>
          </>
        )}
      />

      <section className="api-docs-card" id="quick-start">
        <h3 className="api-docs-card__title">快速开始</h3>
        <div className="api-docs-grid">
          <div>
            <div className="api-docs-field-label">Base URL</div>
            <Paragraph copyable={{ text: BASE_URL_PLACEHOLDER }} className="api-docs-paragraph">
              <Text code>{BASE_URL_PLACEHOLDER}</Text>
            </Paragraph>
          </div>
          <div>
            <div className="api-docs-field-label">认证请求头</div>
            <Paragraph copyable={{ text: `X-API-Key: ${API_KEY_PLACEHOLDER}` }} className="api-docs-paragraph">
              <Text code>X-API-Key: {API_KEY_PLACEHOLDER}</Text>
            </Paragraph>
          </div>
        </div>
        <div className="api-docs-tip">
          <KeyOutlined />
          <span>
            API Key 由管理员在「系统设置 → API Key」中生成,完整 Key 仅在生成时展示一次;OpenAPI
            规范文件可导入 Postman / Apifox / Swagger Editor 直接调试。
          </span>
        </div>
      </section>

      <section className="api-docs-card" id="conventions">
        <h3 className="api-docs-card__title">通用约定</h3>
        <p className="api-docs-text">
          请求体统一使用 <Text code>application/json</Text>。成功响应为
          <Text code>{'{ "success": true, "data": ... }'}</Text>;失败响应为
          <Text code>{'{ "success": false, "code": "...", "message": "..." }'}</Text>。
        </p>
        <Table
          rowKey="code"
          size="small"
          pagination={false}
          dataSource={ERROR_CODE_ROWS}
          columns={[
            { title: 'HTTP 状态码', dataIndex: 'status', width: 120 },
            {
              title: 'code',
              dataIndex: 'code',
              width: 280,
              render: (value: string) => <Text code>{value}</Text>,
            },
            { title: '说明', dataIndex: 'description' },
          ]}
        />
      </section>

      <section className="api-docs-card" id="flow">
        <h3 className="api-docs-card__title">典型集成流程</h3>
        <div className="api-docs-flow">
          <span className="api-docs-flow__step">添加视频源</span>
          <span className="api-docs-flow__arrow">→</span>
          <span className="api-docs-flow__step">查询编排模板</span>
          <span className="api-docs-flow__arrow">→</span>
          <span className="api-docs-flow__step">按模板激活编排</span>
          <span className="api-docs-flow__arrow">→</span>
          <span className="api-docs-flow__step">按需更新流地址 / 去激活编排</span>
        </div>
      </section>

      {ENDPOINTS.map((endpoint, index) => (
        <section className="api-docs-card" key={endpoint.id} id={endpoint.id}>
          <div className="api-docs-endpoint__header">
            <Tag color={METHOD_COLORS[endpoint.method]} className="api-docs-method-tag">
              {endpoint.method}
            </Tag>
            <Text code className="api-docs-endpoint__path">{endpoint.path}</Text>
            <span className="api-docs-endpoint__summary">
              {index + 1}. {endpoint.summary}
            </span>
          </div>
          {endpoint.description ? (
            <p className="api-docs-text">{endpoint.description}</p>
          ) : null}

          {endpoint.queryParams ? (
            <>
              <div className="api-docs-field-label">查询参数</div>
              <Table
                rowKey="name"
                size="small"
                pagination={false}
                dataSource={endpoint.queryParams}
                columns={paramColumns}
              />
            </>
          ) : null}

          {endpoint.bodyParams ? (
            <>
              <div className="api-docs-field-label">请求体</div>
              <Table
                rowKey="name"
                size="small"
                pagination={false}
                dataSource={endpoint.bodyParams}
                columns={paramColumns}
              />
            </>
          ) : null}

          <div className="api-docs-field-label">
            请求示例
            <Button
              size="small"
              type="text"
              icon={<CopyOutlined />}
              onClick={() => void handleCopy(endpoint.curl)}
            >
              复制
            </Button>
          </div>
          <pre className="api-docs-code">{endpoint.curl}</pre>

          <div className="api-docs-field-label">
            响应示例
            {endpoint.responseNote ? (
              <span className="api-docs-response-note">{endpoint.responseNote}</span>
            ) : null}
          </div>
          <pre className="api-docs-code">{endpoint.response}</pre>
        </section>
      ))}

      <div className="api-docs-footer">
        <DownloadOutlined />
        <span>完整字段定义与校验规则请以 OpenAPI 规范文件为准,可在页面右上角下载。</span>
      </div>
    </div>
  );
}
