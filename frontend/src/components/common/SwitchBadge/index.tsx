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
    <div
      className={`switch-badge switch-badge-${size} ${disabled ? 'switch-disabled' : ''}`}
      onClick={handleClick}
      style={{ cursor: disabled ? 'not-allowed' : 'pointer' }}
    >
      <div className={`switch-track ${checked ? 'switch-checked' : ''}`}>
        <div className={`switch-thumb ${checked ? 'thumb-checked' : ''}`} />
      </div>
      <span
        className={`switch-text ${checked ? 'text-checked' : 'text-unchecked'}`}
      >
        {checked ? checkedText : uncheckedText}
      </span>
    </div>
  );
};

export default SwitchBadge;
