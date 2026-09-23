import { tr } from '@/i18n/tr';
import React, { useCallback } from 'react';
import { App } from 'antd';
import type { ModalFuncProps } from 'antd';

export interface AppConfirmOptions {
  tone?: 'danger' | 'info';
  title: React.ReactNode;
  objectName?: React.ReactNode;
  description?: React.ReactNode;
  confirmText?: string;
  cancelText?: string;
  onConfirm: NonNullable<ModalFuncProps['onOk']>;
}

export function useAppConfirm() {
  const { modal } = App.useApp();

  return useCallback(({
    tone = 'danger',
    title,
    objectName,
    description,
    confirmText,
    cancelText = tr("取消"),
    onConfirm,
  }: AppConfirmOptions) => {
    return modal.confirm({
      title,
      content: (
        <div>
          {objectName ? <span className="app-confirm__object">{objectName}</span> : null}
          {description ? <span>{description}</span> : null}
        </div>
      ),
      okText: confirmText ?? (tone === 'danger' ? tr("确认删除") : tr("确认")),
      cancelText,
      okButtonProps: tone === 'danger' ? { danger: true } : undefined,
      centered: true,
      className: `app-confirm-modal app-confirm-modal--${tone}`,
      autoFocusButton: 'cancel',
      onOk: onConfirm,
    });
  }, [modal]);
}
