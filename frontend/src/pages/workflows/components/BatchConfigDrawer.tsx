import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Collapse,
  Divider,
  Drawer,
  InputNumber,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  BellOutlined,
  ClockCircleOutlined,
  ControlOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import Button from '@/components/common/AppButton';
import { useAppConfirm } from '@/components/common';
import {
  batchConfigWorkflows,
  type Workflow,
  type WorkflowBatchConfigTarget,
} from '@/services/api';
import TimeScheduleEditor from './TimeScheduleEditor';
import {
  createDefaultWeeklySchedule,
  normalizeWeeklySchedule,
  type WeeklySchedule,
} from '../utils/timeSchedule';
import './BatchConfigDrawer.css';

const { Text } = Typography;
type SupportedNodeType = 'algorithm' | 'alert' | 'time_schedule';

interface NodeMatch {
  key: string;
  groupLabel: string;
  label: string;
  nodeType: SupportedNodeType;
  workflowNodes: Array<{ workflowId: number; node: any }>;
}

interface NodeDraft {
  applyConfidence?: boolean;
  confidence?: number;
  applyInterval?: boolean;
  intervalSeconds?: number;
  applyTrigger?: boolean;
  triggerEnabled?: boolean;
  triggerWindow?: number;
  triggerMode?: 'count' | 'ratio' | 'consecutive';
  triggerThreshold?: number;
  applySuppression?: boolean;
  suppressionEnabled?: boolean;
  suppressionSeconds?: number;
  applySchedule?: boolean;
  weeklySchedule?: WeeklySchedule;
}

interface BatchConfigDrawerProps {
  open: boolean;
  workflows: Workflow[];
  onClose: () => void;
  onApplied: () => void;
}

const supportedNode = (node: any): node is any & { type: SupportedNodeType } => (
  node && ['algorithm', 'alert', 'time_schedule'].includes(node.type)
);

const directIdentity = (node: any) => (
  node.type === 'algorithm'
    ? `${node.type}:${node.dataId ?? node.algorithmId ?? ''}`
    : node.type
);

const buildNodeMatches = (workflows: Workflow[]): NodeMatch[] => {
  const groups = new Map<string, Workflow[]>();
  workflows.forEach((workflow) => {
    const key = workflow.source_template_id == null ? 'direct' : `template:${workflow.source_template_id}`;
    groups.set(key, [...(groups.get(key) || []), workflow]);
  });

  const matches: NodeMatch[] = [];
  groups.forEach((groupWorkflows, groupKey) => {
    const first = groupWorkflows[0];
    const groupLabel = first.source_template_id == null
      ? '自主创建'
      : first.source_template_name || `模板 #${first.source_template_id}`;
    const firstNodes = (first.workflow_data?.nodes || []).filter(supportedNode);

    if (groupKey !== 'direct') {
      firstNodes.forEach((node: any) => {
        const workflowNodes = groupWorkflows.map((workflow) => ({
          workflowId: workflow.id,
          node: workflow.workflow_data?.nodes?.find((candidate: any) => (
            String(candidate.id) === String(node.id) && candidate.type === node.type
          )),
        }));
        if (workflowNodes.every((item) => item.node)) {
          matches.push({
            key: `${groupKey}:${node.id}`,
            groupLabel,
            label: node.name || node.id,
            nodeType: node.type,
            workflowNodes,
          });
        }
      });
      return;
    }

    const identities = new Map<string, any>();
    firstNodes.forEach((node: any) => {
      const identity = directIdentity(node);
      if (identities.has(identity)) identities.set(identity, null);
      else identities.set(identity, node);
    });
    identities.forEach((node, identity) => {
      if (!node) return;
      const workflowNodes = groupWorkflows.map((workflow) => {
        const candidates = (workflow.workflow_data?.nodes || []).filter((candidate: any) => (
          supportedNode(candidate) && directIdentity(candidate) === identity
        ));
        return { workflowId: workflow.id, node: candidates.length === 1 ? candidates[0] : null };
      });
      if (workflowNodes.every((item) => item.node)) {
        matches.push({
          key: `${groupKey}:${identity}`,
          groupLabel,
          label: node.name || node.id,
          nodeType: node.type,
          workflowNodes,
        });
      }
    });
  });
  return matches;
};

const initialDraft = (match: NodeMatch): NodeDraft => {
  const node = match.workflowNodes[0]?.node || {};
  const config = node.config || {};
  const data = node.data || {};
  const trigger = data.triggerCondition || {};
  const suppression = data.suppression || {};
  return {
    confidence: config.confidence ?? 0.5,
    intervalSeconds: config.interval_seconds ?? 1,
    triggerEnabled: trigger.enable === true,
    triggerWindow: trigger.window_size ?? 30,
    triggerMode: trigger.mode ?? 'ratio',
    triggerThreshold: trigger.threshold ?? 0.3,
    suppressionEnabled: suppression.enable === true,
    suppressionSeconds: suppression.seconds ?? 60,
    weeklySchedule: normalizeWeeklySchedule(data.weeklySchedule || createDefaultWeeklySchedule()),
  };
};

const nodeIcon = (nodeType: SupportedNodeType) => {
  if (nodeType === 'algorithm') return <ThunderboltOutlined />;
  if (nodeType === 'alert') return <BellOutlined />;
  return <ClockCircleOutlined />;
};

const BatchConfigDrawer: React.FC<BatchConfigDrawerProps> = ({
  open,
  workflows,
  onClose,
  onApplied,
}) => {
  const [drafts, setDrafts] = useState<Record<string, NodeDraft>>({});
  const [submitting, setSubmitting] = useState(false);
  const confirmAction = useAppConfirm();
  const matches = useMemo(() => buildNodeMatches(workflows), [workflows]);

  useEffect(() => {
    if (!open) return;
    setDrafts(Object.fromEntries(matches.map((match) => [match.key, initialDraft(match)])));
  }, [matches, open]);

  const updateDraft = (key: string, patch: Partial<NodeDraft>) => {
    setDrafts((current) => ({ ...current, [key]: { ...current[key], ...patch } }));
  };

  const targets = useMemo<WorkflowBatchConfigTarget[]>(() => {
    const result: WorkflowBatchConfigTarget[] = [];
    matches.forEach((match) => {
      const draft = drafts[match.key] || {};
      const changes: Record<string, any> = {};
      if (match.nodeType === 'algorithm') {
        if (draft.applyConfidence) changes.confidence = draft.confidence;
        if (draft.applyInterval) changes.interval_seconds = draft.intervalSeconds;
      }
      if (match.nodeType === 'alert') {
        if (draft.applyTrigger) {
          changes.trigger_condition = draft.triggerEnabled
            ? {
                enable: true,
                window_size: draft.triggerWindow,
                mode: draft.triggerMode,
                threshold: draft.triggerThreshold,
              }
            : { enable: false };
        }
        if (draft.applySuppression) {
          changes.suppression = draft.suppressionEnabled
            ? { enable: true, seconds: draft.suppressionSeconds }
            : { enable: false };
        }
      }
      if (match.nodeType === 'time_schedule' && draft.applySchedule) {
        changes.weekly_schedule = draft.weeklySchedule;
      }
      if (Object.keys(changes).length === 0) return;

      const byNodeId = new Map<string, number[]>();
      match.workflowNodes.forEach(({ workflowId, node }) => {
        const nodeId = String(node.id);
        byNodeId.set(nodeId, [...(byNodeId.get(nodeId) || []), workflowId]);
      });
      byNodeId.forEach((workflowIds, nodeId) => {
        result.push({
          workflow_ids: workflowIds,
          node_id: nodeId,
          node_type: match.nodeType,
          changes,
        });
      });
    });
    return result;
  }, [drafts, matches]);

  const formatError = (error: any) => {
    const failures = error?.data?.failures;
    if (Array.isArray(failures) && failures.length > 0) {
      return failures.slice(0, 3).map((item: any) => item.error).join('；');
    }
    return error?.data?.error || error?.message || '批量配置失败';
  };

  const handleSubmit = async () => {
    if (targets.length === 0) {
      message.warning('请先勾选至少一个要应用的参数');
      return;
    }
    const payload = {
      workflow_ids: workflows.map((workflow) => workflow.id),
      expected_versions: Object.fromEntries(
        workflows.map((workflow) => [String(workflow.id), workflow.config_version]),
      ),
      targets,
      dry_run: true,
    };
    setSubmitting(true);
    try {
      const preview = await batchConfigWorkflows(payload);
      const { summary } = preview;
      confirmAction({
        tone: 'info',
        title: '应用批量配置',
        objectName: `${summary.workflow_count} 个编排 · ${summary.node_change_count} 处节点修改`,
        description: summary.active_count > 0
          ? `其中 ${summary.active_count} 个正在运行，保存后将按配置版本自动重载。`
          : '所有目标编排当前均已停用。',
        confirmText: '确认应用',
        onConfirm: async () => {
          try {
            const applied = await batchConfigWorkflows({ ...payload, dry_run: false });
            message.success(applied.message || `已更新 ${applied.summary.workflow_count} 个编排`);
            onApplied();
            onClose();
          } catch (error: any) {
            message.error(formatError(error));
            throw error;
          }
        },
      });
    } catch (error: any) {
      message.error(formatError(error));
    } finally {
      setSubmitting(false);
    }
  };

  const collapseItems = matches.map((match) => {
    const draft = drafts[match.key] || initialDraft(match);
    return {
      key: match.key,
      label: (
        <div className="batch-config-node-title">
          <span className={`batch-config-node-icon is-${match.nodeType}`}>{nodeIcon(match.nodeType)}</span>
          <div>
            <strong>{match.label}</strong>
            <span>{match.groupLabel} · 覆盖 {match.workflowNodes.length} 个编排</span>
          </div>
          <Tag>{match.nodeType === 'algorithm' ? '算法' : match.nodeType === 'alert' ? '告警' : '时间计划'}</Tag>
        </div>
      ),
      children: match.nodeType === 'algorithm' ? (
        <div className="batch-config-fields">
          <div className="batch-config-field">
            <Switch checked={draft.applyConfidence} onChange={(checked) => updateDraft(match.key, { applyConfidence: checked })} />
            <div><strong>覆盖置信度</strong><span>仅修改当前算法节点的检测阈值</span></div>
            <InputNumber min={0} max={1} step={0.1} value={draft.confidence} disabled={!draft.applyConfidence} onChange={(value) => updateDraft(match.key, { confidence: value ?? 0.5 })} />
          </div>
          <div className="batch-config-field">
            <Switch checked={draft.applyInterval} onChange={(checked) => updateDraft(match.key, { applyInterval: checked })} />
            <div><strong>覆盖检测间隔</strong><span>0.1–60 秒</span></div>
            <InputNumber min={0.1} max={60} step={0.1} addonAfter="秒" value={draft.intervalSeconds} disabled={!draft.applyInterval} onChange={(value) => updateDraft(match.key, { intervalSeconds: value ?? 1 })} />
          </div>
        </div>
      ) : match.nodeType === 'alert' ? (
        <div className="batch-config-fields">
          <div className="batch-config-section-switch">
            <Switch checked={draft.applyTrigger} onChange={(checked) => updateDraft(match.key, { applyTrigger: checked })} />
            <div><strong>覆盖窗口检测</strong><span>统一触发窗口、统计方式和阈值</span></div>
          </div>
          {draft.applyTrigger ? (
            <div className="batch-config-subform">
              <Space><Text>启用窗口检测</Text><Switch checked={draft.triggerEnabled} onChange={(checked) => updateDraft(match.key, { triggerEnabled: checked })} /></Space>
              {draft.triggerEnabled ? <>
                <label>时间窗口<InputNumber min={1} max={300} addonAfter="秒" value={draft.triggerWindow} onChange={(value) => updateDraft(match.key, { triggerWindow: value ?? 30 })} /></label>
                <label>检测模式<Select value={draft.triggerMode} onChange={(value) => updateDraft(match.key, { triggerMode: value, triggerThreshold: value === 'ratio' ? 0.3 : 3 })} options={[{ value: 'ratio', label: '检测比例' }, { value: 'count', label: '检测次数' }, { value: 'consecutive', label: '连续检测' }]} /></label>
                <label>检测阈值<InputNumber min={draft.triggerMode === 'ratio' ? 0 : 1} max={draft.triggerMode === 'ratio' ? 1 : 100} step={draft.triggerMode === 'ratio' ? 0.05 : 1} value={draft.triggerThreshold} onChange={(value) => updateDraft(match.key, { triggerThreshold: value ?? 1 })} /></label>
              </> : null}
            </div>
          ) : null}
          <Divider />
          <div className="batch-config-section-switch">
            <Switch checked={draft.applySuppression} onChange={(checked) => updateDraft(match.key, { applySuppression: checked })} />
            <div><strong>覆盖告警抑制</strong><span>统一告警触发后的冷却时间</span></div>
          </div>
          {draft.applySuppression ? (
            <div className="batch-config-subform">
              <Space><Text>启用告警抑制</Text><Switch checked={draft.suppressionEnabled} onChange={(checked) => updateDraft(match.key, { suppressionEnabled: checked })} /></Space>
              {draft.suppressionEnabled ? <label>抑制时长<InputNumber min={1} max={3600} addonAfter="秒" value={draft.suppressionSeconds} onChange={(value) => updateDraft(match.key, { suppressionSeconds: value ?? 60 })} /></label> : null}
            </div>
          ) : null}
        </div>
      ) : (
        <div className="batch-config-fields">
          <div className="batch-config-section-switch">
            <Switch checked={draft.applySchedule} onChange={(checked) => updateDraft(match.key, { applySchedule: checked })} />
            <div><strong>覆盖周计划</strong><span>整周计划将替换选中编排中的当前设置</span></div>
          </div>
          {draft.applySchedule ? <TimeScheduleEditor value={draft.weeklySchedule} onChange={(value) => updateDraft(match.key, { weeklySchedule: value })} /> : null}
        </div>
      ),
    };
  });

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={720}
      rootClassName="batch-config-drawer-root"
      className="batch-config-drawer"
      title={<div className="batch-config-heading"><ControlOutlined /><div><strong>批量配置</strong><span>只覆盖明确勾选的公共参数</span></div></div>}
      extra={<Button onClick={onClose} disabled={submitting}>取消</Button>}
      footer={(
        <div className="batch-config-footer">
          <span>已选择 {workflows.length} 个编排，配置 {targets.length} 组节点参数</span>
          <Button type="primary" icon={<ControlOutlined />} loading={submitting} disabled={matches.length === 0} onClick={handleSubmit}>预览并应用</Button>
        </div>
      )}
    >
      <Alert
        type="info"
        showIcon
        message="字段级覆盖"
        description="未打开“覆盖”开关的字段、视频源绑定和编排结构都不会改变。正式保存前会完成整批预检。"
      />
      {matches.length > 0 ? (
        <Collapse className="batch-config-collapse" items={collapseItems} defaultActiveKey={[matches[0].key]} />
      ) : (
        <Alert className="batch-config-empty" type="warning" showIcon message="没有共同的可配置节点" description="所选编排来自不同结构，或自主创建的同类节点无法唯一匹配。请缩小选择范围后重试。" />
      )}
    </Drawer>
  );
};

export default BatchConfigDrawer;
