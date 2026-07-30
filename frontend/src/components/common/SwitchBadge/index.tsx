import React from 'react';
import './index.css';

export interface SwitchBadgeProps {
  checked: boolean;
  onChange?: (checked: boolean) => void;
  disabled?: boolean;
  checkedText?: string;
  uncheckedText?: string;
  size?: 'small' | 'default' | 'large';
}

const SwitchBadge: React.FC<SwitchBadgeProps> = ({
  checked,
  onChange,
  disabled = false,
  checkedText = '启用',
  uncheckedText = '禁用',
  size = 'default',
}) => {
  const handleClick = () => {
    if (!disabled && onChange) {
      onChange(!checked);
    }
  };

  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      className={`switch-badge switch-badge-${size} ${checked ? 'switch-checked' : ''} ${disabled ? 'switch-disabled' : ''}`}
      onClick={handleClick}
    >
      <div className={`switch-track ${checked ? 'switch-checked' : ''}`}>
        <div className={`switch-thumb ${checked ? 'thumb-checked' : ''}`} />
      </div>
      <span
        className={`switch-text ${checked ? 'text-checked' : 'text-unchecked'}`}
      >
        {checked ? checkedText : uncheckedText}
      </span>
    </button>
  );
};

export default SwitchBadge;
