import React, { memo, useDeferredValue, useMemo, useState } from 'react';
import { Input, Tree } from 'antd';
import type { DataNode } from 'antd/es/tree';
import {
  ApartmentOutlined,
  FolderOpenOutlined,
  VideoCameraOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import type { Workflow } from '@/services/api';
import './WorkflowSourceTree.css';

export type WorkflowTreeKey = 'all' | 'templates' | 'unbound' | `source:${number}`;

interface WorkflowSourceTreeProps {
  workflows: Workflow[];
  videoSources: any[];
  selectedKey: WorkflowTreeKey;
  onSelect: (key: WorkflowTreeKey) => void;
}

const getSourceId = (workflow: Workflow): number | null => {
  const sourceNode = workflow.workflow_data?.nodes?.find((node: any) => node.type === 'source');
  const value = workflow.video_source_id ?? sourceNode?.dataId;
  return value == null || value === '' ? null : Number(value);
};

const WorkflowSourceTree: React.FC<WorkflowSourceTreeProps> = ({
  workflows,
  videoSources,
  selectedKey,
  onSelect,
}) => {
  const [searchText, setSearchText] = useState('');
  const deferredSearch = useDeferredValue(searchText.trim().toLowerCase());

  const counts = useMemo(() => {
    const sourceCounts = new Map<number, number>();
    let unboundCount = 0;
    workflows.forEach((workflow) => {
      if (workflow.is_template) return; // 模板在独立分区展示，不参与视频源计数
      const sourceId = getSourceId(workflow);
      if (sourceId == null) {
        unboundCount += 1;
      } else {
        sourceCounts.set(sourceId, (sourceCounts.get(sourceId) || 0) + 1);
      }
    });
    return { sourceCounts, unboundCount };
  }, [workflows]);

  const treeData = useMemo<DataNode[]>(() => {
    const matchingSources = videoSources.filter((source) => (
      !deferredSearch || String(source.name || '').toLowerCase().includes(deferredSearch)
    ));
    const children: DataNode[] = [];
    matchingSources.forEach((source) => {
      const count = counts.sourceCounts.get(Number(source.id)) || 0;
      children.push({
        key: `source:${source.id}`,
        icon: <VideoCameraOutlined />,
        title: (
          <span className={`workflow-tree-title ${count === 0 ? 'is-empty' : ''}`}>
            <span title={source.name}>{source.name}</span><b>{count}</b>
          </span>
        ),
      });
    });
    if (counts.unboundCount > 0 && (!deferredSearch || '未绑定视频源'.includes(deferredSearch))) {
      children.push({
        key: 'unbound',
        icon: <WarningOutlined />,
        title: <span className="workflow-tree-title"><span>未绑定视频源</span><b>{counts.unboundCount}</b></span>,
      });
    }
    return [{
      key: 'all',
      icon: <FolderOpenOutlined />,
      title: <span className="workflow-tree-title workflow-tree-root"><span>全部编排</span><b>{workflows.length}</b></span>,
      children,
    }];
  }, [counts, deferredSearch, videoSources, workflows.length]);

  return (
    <div className="workflow-source-tree">
      <div className="workflow-source-tree__heading">
        <span className="workflow-source-tree__icon"><ApartmentOutlined /></span>
        <div>
          <strong>视频源</strong>
          <span>按绑定关系快速定位</span>
        </div>
      </div>
      <Input.Search
        aria-label="搜索视频源"
        allowClear
        placeholder="搜索视频源"
        value={searchText}
        onChange={(event) => setSearchText(event.target.value)}
      />
      <Tree
        blockNode
        showIcon
        defaultExpandAll
        selectedKeys={[selectedKey]}
        treeData={treeData}
        onSelect={(keys) => {
          if (keys[0]) onSelect(String(keys[0]) as WorkflowTreeKey);
        }}
      />
    </div>
  );
};

export default memo(WorkflowSourceTree);
