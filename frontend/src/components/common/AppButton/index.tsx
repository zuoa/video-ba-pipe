import React from 'react';
import { Button } from 'antd';
import type { ButtonProps } from 'antd';
import './index.css';

export type SemanticTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger';
export type AppButtonVariant = 'solid' | 'outline' | 'text';

export interface AppButtonProps extends Omit<ButtonProps, 'variant'> {
  tone?: SemanticTone;
  variant?: AppButtonVariant;
  iconOnly?: boolean;
}

const AppButton: React.FC<AppButtonProps> = ({
  tone,
  variant,
  iconOnly = false,
  type,
  danger,
  className,
  children,
  ...props
}) => {
  const legacyTone: SemanticTone =
    className?.match(/delete|remove|clear/)
      ? 'danger'
      : className?.match(/warning/)
        ? 'warning'
        : className?.match(/edit|view|upload|preview/)
          ? 'info'
          : 'neutral';
  const resolvedTone: SemanticTone = tone ?? (danger ? 'danger' : legacyTone);
  const resolvedVariant: AppButtonVariant =
    variant ?? (type === 'primary' ? 'solid' : type === 'text' || type === 'link' ? 'text' : 'outline');
  const resolvedType: ButtonProps['type'] =
    resolvedVariant === 'solid'
      ? 'primary'
      : resolvedVariant === 'text'
        ? type === 'link'
          ? 'link'
          : 'text'
        : 'default';

  return (
    <Button
      {...props}
      type={resolvedType}
      danger={resolvedTone === 'danger'}
      className={[
        'app-button',
        `app-button--${resolvedTone}`,
        `app-button--${resolvedVariant}`,
        iconOnly ? 'app-button--icon-only' : '',
        className,
      ].filter(Boolean).join(' ')}
    >
      {children}
    </Button>
  );
};

export default AppButton;
