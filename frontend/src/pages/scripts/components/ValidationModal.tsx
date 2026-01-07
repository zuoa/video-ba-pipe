import React from 'react';
import { Modal } from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  MinusCircleOutlined,
} from '@ant-design/icons';
import './ValidationModal.css';

export interface ValidationModalProps {
  visible: boolean;
  validation?: any;
  onClose: () => void;
}

const ValidationModal: React.FC<ValidationModalProps> = ({
  visible,
  validation,
  onClose,
}) => {
  if (!validation) return null;

  const items = [
    { label: '语法检查', passed: validation.syntax_valid, error: validation.syntax_error, optional: false },
    { label: 'process函数', passed: validation.has_process, optional: false },
    { label: 'init函数', passed: validation.has_init, optional: true },
    { label: 'cleanup函数', passed: validation.has_cleanup, optional: true },
    { label: 'SCRIPT_METADATA', passed: validation.has_metadata, optional: false },
    { label: '整体评估', passed: validation.is_valid, optional: false },
  ];

  return (
    <Modal
      title={
        <div className="validation-modal-title">
          <div className="validation-icon">
            <CheckCircleOutlined />
          </div>
          <span>语法验证结果</span>
        </div>
      }
      open={visible}
      onCancel={onClose}
      onOk={onClose}
      okText="关闭"
      cancelButtonProps={{ style: { display: 'none' } }}
      className="validation-modal"
      centered
    >
      <div className="validation-content">
        {items.map((item, index) => (
          <div key={index} className="validation-item">
            <div className="validation-item-header">
              <span className="validation-label">{item.label}</span>
              <span className={`validation-status ${item.passed ? 'passed' : item.optional ? 'optional' : 'failed'}`}>
                {item.passed ? (
                  <CheckCircleOutlined />
                ) : item.optional ? (
                  <MinusCircleOutlined />
                ) : (
                  <CloseCircleOutlined />
                )}
              </span>
            </div>
            {item.error && !item.passed && (
              <div className="validation-error">
                第 {item.error.line} 行: {item.error.message}
              </div>
            )}
          </div>
        ))}
      </div>
    </Modal>
  );
};

export default ValidationModal;
