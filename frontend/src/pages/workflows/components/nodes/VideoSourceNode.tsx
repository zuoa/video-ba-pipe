import React, { memo } from 'react';
import { Handle, Position } from 'reactflow';
import { VideoCameraOutlined } from '@ant-design/icons';
import './BaseNode.css';

const VideoSourceNode = ({ data }: any) => {
  console.log('🎨 VideoSourceNode render, 接收到的data:', {
    完整data: data,
    videoSourceId: data.videoSourceId,
    videoSourceName: data.videoSourceName,
    videoSourceCode: data.videoSourceCode,
    type: data.type,
    label: data.label,
  });

  // 检查是否配置了视频源
  const isConfigured = !!data.videoSourceId;
  const sourceName = data.videoSourceName;
  const hasSourceCode = data.videoSourceCode;

  console.log('📊 VideoSourceNode 显示逻辑:', {
    isConfigured,
    sourceName,
    hasSourceCode,
    会显示名称: !!sourceName,
    会显示编码: !!hasSourceCode,
    会显示ID: !sourceName && !hasSourceCode && isConfigured,
  });

  // 如果配置了视频源，显示详细信息
  const renderSourceInfo = () => {
    if (!isConfigured) {
      return (
        <div className="node-meta" style={{ color: '#ff4d4f' }}>
          <span className="meta-label">⚠️ 未配置视频源</span>
        </div>
      );
    }

    // 显示视频源名称和编码
    return (
      <div className="node-meta" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: '2px' }}>
        {sourceName && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span className="meta-label">名称:</span>
            <span className="meta-value" style={{ fontWeight: 500 }}>{sourceName}</span>
          </div>
        )}
        {hasSourceCode && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span className="meta-label">编码:</span>
            <span className="meta-value" style={{ fontSize: 12, color: '#8c8c8c' }}>{data.videoSourceCode}</span>
          </div>
        )}
        {!sourceName && !hasSourceCode && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span className="meta-label">ID:</span>
            <span className="meta-value" style={{ fontSize: 12 }}>{data.videoSourceId}</span>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="custom-node video-source-node">
      <Handle type="source" position={Position.Right} id="output" className="node-handle" />
      <div className="node-header">
        <VideoCameraOutlined className="node-icon" />
        <span className="node-title">视频源</span>
      </div>
      {data.description && (
        <div className="node-description">{data.description}</div>
      )}
      {renderSourceInfo()}
    </div>
  );
};

export default memo(VideoSourceNode);
