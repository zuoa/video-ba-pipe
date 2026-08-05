export interface TimePeriod {
  start: string;
  end: string;
}

export type WeeklySchedule = Record<string, TimePeriod[]>;

export const WEEKDAYS = [
  { key: '1', label: '周一' },
  { key: '2', label: '周二' },
  { key: '3', label: '周三' },
  { key: '4', label: '周四' },
  { key: '5', label: '周五' },
  { key: '6', label: '周六' },
  { key: '7', label: '周日' },
] as const;

export const createDefaultWeeklySchedule = (): WeeklySchedule =>
  Object.fromEntries(
    WEEKDAYS.map(({ key }) => [key, [{ start: '00:00', end: '23:59' }]]),
  );

export const normalizeWeeklySchedule = (value?: WeeklySchedule): WeeklySchedule =>
  Object.fromEntries(
    WEEKDAYS.map(({ key }) => [
      key,
      Array.isArray(value?.[key])
        ? value[key].map(period => ({ start: period.start, end: period.end }))
        : [],
    ]),
  );

export const summarizeWeeklySchedule = (schedule?: WeeklySchedule): string => {
  const normalized = normalizeWeeklySchedule(schedule);
  const activeDays = WEEKDAYS.filter(({ key }) => normalized[key].length > 0);
  const periods = activeDays.reduce((total, { key }) => total + normalized[key].length, 0);
  if (activeDays.length === 7 && periods === 7 && activeDays.every(({ key }) => (
    normalized[key][0]?.start === '00:00' && normalized[key][0]?.end === '23:59'
  ))) {
    return '每天 · 全天启用';
  }
  return `${activeDays.length} 天 · ${periods} 个时段`;
};
