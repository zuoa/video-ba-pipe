import React, { useState, useMemo } from 'react';
import { Input, List, Tag, Space, Typography, Badge } from 'antd';
import { SearchOutlined, VideoCameraOutlined, CheckCircleOutlined } from '@ant-design/icons';
import './VideoSourceSelector.css';
import AppEmptyState from '@/components/common/AppEmptyState';
import AppModal from '@/components/common/AppModal';

const { Search } = Input;
const { Text } = Typography;

export interface VideoSourceSelectorProps {
  visible: boolean;
  value?: number | null;
  videoSources: any[];
  onChange: (value: number) => void;
  onCancel: () => void;
}

const VideoSourceSelector: React.FC<VideoSourceSelectorProps> = ({
  visible,
  value,
  videoSources,
  onChange,
  onCancel,
}) => {
  const [searchText, setSearchText] = useState('');

  // 过滤视频源
  const filteredSources = useMemo(() => {
    if (!searchText) {
      return videoSources;
    }

    const lowerSearch = searchText.toLowerCase();
    return videoSources.filter((source) => {
      return (
        source.name?.toLowerCase().includes(lowerSearch) ||
        source.source_code?.toLowerCase().includes(lowerSearch) ||
        String(source.id)?.includes(lowerSearch)
      );
    });
  }, [videoSources, searchText]);

  // 获取状态标签
  const getStatusTag = (source: any) => {
    const isActive = source.status === 'running' || source.status === 'active';
    return (
      <Tag color={isActive ? 'success' : 'default'} style={{ margin: 0 }}>
        {isActive ? '运行中' : '未启动'}
      </Tag>
    );
  };

  // 获取协议标签
  const getProtocolTag = (url: string) => {
    if (!url) return null;

    if (url.startsWith('rtsp://')) {
      return <Tag color="blue">RTSP</Tag>;
    } else if (url.includes('flv')) {
      return <Tag color="cyan">HTTP-FLV</Tag>;
    } else if (url.includes('m3u8')) {
      return <Tag color="purple">HLS</Tag>;
    } else if (url.startsWith('/') || url.startsWith('file://')) {
      return <Tag color="orange">本地文件</Tag>;
    }
    return <Tag color="default">其他</Tag>;
  };

  const selectSource = (sourceId: number) => {
    onChange(sourceId);
    setSearchText('');
    onCancel();
  };

  // 渲染视频源项
  const renderSourceItem = (source: any) => {
    // 使用宽松相等 == 来匹配，避免类型不一致（字符串 vs 数字）导致的匹配失败
    const isSelected = source.id == value;

    return (
      <List.Item
        key={source.id}
        className={`source-item ${isSelected ? 'selected' : ''}`}
        role="button"
        tabIndex={0}
        aria-pressed={isSelected}
        onClick={() => selectSource(source.id)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            selectSource(source.id);
          }
        }}
      >
        <div className="source-item-content">
          <div className="source-item-header">
            <Space size="small">
              {isSelected && <CheckCircleOutlined style={{ color: '#52c41a', fontSize: 16 }} />}
              <VideoCameraOutlined style={{ fontSize: 16 }} />
              <Text strong style={{ fontSize: 14 }}>
                {source.name || `未命名视频源 #${source.id}`}
              </Text>
            </Space>
            <Space size="small">
              {getStatusTag(source)}
              {getProtocolTag(source.url)}
            </Space>
          </div>

          <div className="source-item-details">
            {source.source_code && (
              <div className="detail-row">
                <Text type="secondary" style={{ fontSize: 12 }}>
                  编码: {source.source_code}
                </Text>
              </div>
            )}

            <div className="detail-row">
              <Text type="secondary" style={{ fontSize: 12 }}>
                ID: {source.id}
              </Text>
              {source.decoder_type && (
                <Text type="secondary" style={{ fontSize: 12, marginLeft: 16 }}>
                  解码器: {source.decoder_type}
                </Text>
              )}
            </div>

            {source.url && (
              <div className="detail-row">
                <Text type="secondary" ellipsis style={{ fontSize: 12, maxWidth: 400 }}>
                  URL: {source.url}
                </Text>
              </div>
            )}
          </div>
        </div>
      </List.Item>
    );
  };

  return (
    <AppModal
      title="选择视频源"
      description="按名称、编码或 ID 查找可用输入源"
      open={visible}
      onCancel={onCancel}
      footer={null}
      size="lg"
      className="video-source-selector-modal"
    >
      <div className="video-source-selector">
        <Search
          placeholder="搜索视频源名称、编码或ID"
          allowClear
          prefix={<SearchOutlined />}
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          style={{ marginBottom: 16 }}
          size="large"
        />

        <div className="sources-count">
          <Text type="secondary">
            找到 <Text strong>{filteredSources.length}</Text> 个视频源
          </Text>
        </div>

        {filteredSources.length === 0 ? (
          <AppEmptyState
            compact
            title={searchText ? '未找到匹配的视频源' : '暂无可用视频源'}
            description={searchText ? '请尝试调整搜索关键词' : '请先在视频源管理中添加视频源'}
          />
        ) : (
          <List
            className="sources-list"
            dataSource={filteredSources}
            renderItem={renderSourceItem}
            split
          />
        )}
      </div>
    </AppModal>
  );
};

export default VideoSourceSelector;
