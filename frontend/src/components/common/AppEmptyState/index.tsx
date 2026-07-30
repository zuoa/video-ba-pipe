import React from 'react';
import { Empty } from 'antd';
import type { EmptyProps } from 'antd';
import './index.css';

export interface AppEmptyStateProps extends Omit<EmptyProps, 'description'> {
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
  compact?: boolean;
}

const AppEmptyState: React.FC<AppEmptyStateProps> = ({
  title,
  description,
  action,
  compact = false,
  className,
  ...props
}) => (
  <div className={['app-empty-state', compact ? 'app-empty-state--compact' : '', className].filter(Boolean).join(' ')}>
    <Empty
      {...props}
      description={
        <div className="app-empty-state__copy">
          <div className="app-empty-state__title">{title}</div>
          {description ? <div className="app-empty-state__description">{description}</div> : null}
        </div>
      }
    >
      {action}
    </Empty>
  </div>
);

export default AppEmptyState;
