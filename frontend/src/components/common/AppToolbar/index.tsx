import React from 'react';
import './index.css';

export interface AppToolbarProps {
  children: React.ReactNode;
  actions?: React.ReactNode;
  summary?: React.ReactNode;
  className?: string;
}

const AppToolbar: React.FC<AppToolbarProps> = ({
  children,
  actions,
  summary,
  className,
}) => (
  <div className={['app-toolbar', className].filter(Boolean).join(' ')}>
    <div className="app-toolbar__filters">{children}</div>
    {summary ? <div className="app-toolbar__summary">{summary}</div> : null}
    {actions ? <div className="app-toolbar__actions">{actions}</div> : null}
  </div>
);

export default AppToolbar;
