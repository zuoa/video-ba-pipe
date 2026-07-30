import React from 'react';
import { Modal } from 'antd';
import type { ModalProps } from 'antd';
import './index.css';

export type AppModalKind = 'form' | 'detail' | 'inspect' | 'media' | 'fullscreen';
export type AppModalSize = 'sm' | 'md' | 'lg' | 'xl' | 'full';

export interface AppModalProps extends Omit<ModalProps, 'width'> {
  kind?: AppModalKind;
  size?: AppModalSize;
  width?: ModalProps['width'];
  description?: React.ReactNode;
  bodyMode?: 'scroll' | 'compact' | 'canvas';
}

const SIZE_WIDTHS: Record<AppModalSize, ModalProps['width']> = {
  sm: 480,
  md: 640,
  lg: 820,
  xl: 1120,
  full: 'calc(100vw - 40px)',
};

const AppModal: React.FC<AppModalProps> = ({
  kind = 'form',
  size = 'md',
  width,
  title,
  description,
  bodyMode = 'scroll',
  className,
  centered,
  maskClosable,
  keyboard = true,
  ...props
}) => {
  const resolvedTitle = title ? (
    <div className="app-modal__heading">
      <span className="app-modal__signal" aria-hidden="true" />
      <div className="app-modal__heading-copy">
        <div className="app-modal__title">{title}</div>
        {description ? <div className="app-modal__description">{description}</div> : null}
      </div>
    </div>
  ) : undefined;

  return (
    <Modal
      {...props}
      title={resolvedTitle}
      width={width ?? SIZE_WIDTHS[size]}
      centered={centered ?? kind !== 'fullscreen'}
      maskClosable={maskClosable ?? (kind === 'detail' || kind === 'media')}
      keyboard={keyboard}
      className={[
        'app-modal',
        `app-modal--${kind}`,
        `app-modal--body-${bodyMode}`,
        className,
      ].filter(Boolean).join(' ')}
    />
  );
};

export default AppModal;
