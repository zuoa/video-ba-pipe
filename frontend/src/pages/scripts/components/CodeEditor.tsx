import React from 'react';
import { Input } from 'antd';
import MonacoEditor from '@monaco-editor/react';

const { TextArea } = Input;

export interface CodeEditorProps {
  value: string;
  onChange: (value: string) => void;
  height?: number;
}

const CodeEditor: React.FC<CodeEditorProps> = ({ value, onChange, height = 400 }) => {
  return (
    <div className="code-editor-wrapper" style={{ height }}>
      <MonacoEditor
        height="100%"
        language="python"
        theme="vs-dark"
        value={value}
        onChange={(newValue) => onChange(newValue || '')}
        options={{
          minimap: { enabled: false },
          fontSize: 14,
          lineNumbers: 'on',
          scrollBeyondLastLine: false,
          automaticLayout: true,
          tabSize: 4,
          wordWrap: 'on',
          formatOnPaste: true,
          formatOnType: true,
        }}
      />
    </div>
  );
};

export default CodeEditor;
