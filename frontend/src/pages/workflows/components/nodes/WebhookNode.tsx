import React, { memo } from 'react';
import { Handle, Position } from 'reactflow';
import { SendOutlined } from '@ant-design/icons';
import './BaseNode.css';

const PROVIDER_LABELS: Record<string, string> = {
  generic: '通用 JSON',
  dingtalk: '钉钉',
  bark: 'Bark',
};

const WebhookNode = ({ data }: any) => {
  const config = data.config || {};
  const configured = Boolean(config.endpoint_url || config.endpoint_url_configured);

  return (
    <div className="custom-node webhook-node">
      <Handle type="target" position={Position.Left} id="input" className="node-handle" />
      <div className="node-header">
        <SendOutlined className="node-icon" />
        <span className="node-title">{data.label}</span>
      </div>
      {data.description ? <div className="node-description">{data.description}</div> : null}
      <div className="node-meta">
        <span className="meta-label">协议:</span>
        <span className="meta-value">{PROVIDER_LABELS[config.provider || 'generic'] || config.provider}</span>
      </div>
      <div className="node-meta">
        <span className="meta-label">端点:</span>
        <span className="meta-value">{configured ? '已配置' : '未配置'}</span>
      </div>
    </div>
  );
};

export default memo(WebhookNode);
