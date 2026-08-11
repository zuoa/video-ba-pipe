import type { Workflow } from '@/services/api';

export type SourceFilter = 'all' | 'unbound' | `source:${number}`;
export type TemplateOriginFilter = 'all' | 'direct' | `template:${number}`;
export type RuntimeStatusFilter = 'all' | 'active' | 'inactive';

export interface RuntimeWorkflowFilters {
  search: string;
  source: SourceFilter;
  origin: TemplateOriginFilter;
  status: RuntimeStatusFilter;
}

export const defaultRuntimeFilters: RuntimeWorkflowFilters = {
  search: '',
  source: 'all',
  origin: 'all',
  status: 'all',
};

export const getWorkflowSourceId = (workflow: Workflow): number | null => {
  const sourceNode = workflow.workflow_data?.nodes?.find((node: any) => node.type === 'source');
  const value = workflow.video_source_id ?? sourceNode?.dataId ?? sourceNode?.data?.dataId;
  if (value == null || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

export const workflowMatchesSearch = (workflow: Workflow, search: string) => {
  const normalizedSearch = search.trim().toLocaleLowerCase('zh-CN');
  if (!normalizedSearch) return true;
  return [workflow.name, workflow.description]
    .filter(Boolean)
    .some((value) => String(value).toLocaleLowerCase('zh-CN').includes(normalizedSearch));
};

export const filterRuntimeWorkflows = (
  workflows: Workflow[],
  filters: RuntimeWorkflowFilters,
) => workflows.filter((workflow) => {
  if (workflow.is_template || !workflowMatchesSearch(workflow, filters.search)) return false;

  const sourceId = getWorkflowSourceId(workflow);
  const matchesSource = filters.source === 'all'
    || (filters.source === 'unbound' && sourceId == null)
    || (filters.source.startsWith('source:') && sourceId === Number(filters.source.slice(7)));
  if (!matchesSource) return false;

  const matchesOrigin = filters.origin === 'all'
    || (filters.origin === 'direct' && workflow.source_template_id == null)
    || (filters.origin.startsWith('template:')
      && workflow.source_template_id === Number(filters.origin.slice(9)));
  if (!matchesOrigin) return false;

  return filters.status === 'all'
    || (filters.status === 'active' && workflow.is_active)
    || (filters.status === 'inactive' && !workflow.is_active);
});

export const hasActiveRuntimeFilters = (filters: RuntimeWorkflowFilters) => (
  Boolean(filters.search.trim())
  || filters.source !== 'all'
  || filters.origin !== 'all'
  || filters.status !== 'all'
);
