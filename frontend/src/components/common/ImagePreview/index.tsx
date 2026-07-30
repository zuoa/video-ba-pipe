import React, { useState, useEffect } from 'react';
import { Image } from 'antd';
import { CloseOutlined, WarningOutlined } from '@ant-design/icons';
import AppModal from '../AppModal';
import './index.css';

export interface ImagePreviewProps {
  visible: boolean;
  src: string;
  alt?: string;
  title?: string;
  onClose: () => void;
}

const ImagePreview: React.FC<ImagePreviewProps> = ({
  visible,
  src,
  alt = '预览图片',
  title,
  onClose,
}) => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (visible) {
      setLoading(true);
      setError(false);
    }
  }, [visible, src]);

  return (
    <AppModal
      open={visible}
      onCancel={onClose}
      footer={null}
      kind="media"
      size="full"
      centered
      width="min(1200px, calc(100vw - 40px))"
      className="image-preview-modal"
      closeIcon={
        <div className="preview-close-btn">
          <CloseOutlined />
        </div>
      }
    >
      <div className="preview-container">
        {title && <div className="preview-title">{title}</div>}
        <div className="preview-image-wrapper">
          {error ? (
            <div className="preview-error">
              <WarningOutlined className="error-icon" />
              <div className="error-text">无法加载预览图片</div>
            </div>
          ) : (
            <Image
              src={src}
              alt={alt}
              className={`preview-image ${loading ? 'loading' : ''}`}
              style={{ display: loading ? 'none' : 'block' }}
              preview={false}
              onLoad={() => setLoading(false)}
              onError={() => {
                setLoading(false);
                setError(true);
              }}
            />
          )}
          {loading && !error && (
            <div className="preview-loading">
              <div className="loading-spinner" />
              <div className="loading-text">加载中...</div>
            </div>
          )}
        </div>
      </div>
    </AppModal>
  );
};

export default ImagePreview;
