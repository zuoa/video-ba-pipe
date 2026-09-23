import { tr, trf } from '@/i18n/tr';
import React, { memo } from 'react';
import { Input, Space } from 'antd';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import Button from '@/components/common/AppButton';
import {
  normalizeWeeklySchedule,
  TimePeriod,
  WEEKDAYS,
  WeeklySchedule,
} from '../utils/timeSchedule';
import './TimeScheduleEditor.css';

interface TimeScheduleEditorProps {
  value?: WeeklySchedule;
  onChange?: (value: WeeklySchedule) => void;
}

const TimeScheduleEditor: React.FC<TimeScheduleEditorProps> = ({ value, onChange }) => {
  const schedule = normalizeWeeklySchedule(value);

  const updateDay = (day: string, periods: TimePeriod[]) => {
    onChange?.({ ...schedule, [day]: periods });
  };

  const updatePeriod = (day: string, index: number, field: keyof TimePeriod, nextValue: string) => {
    const periods = schedule[day].map((period, periodIndex) => (
      periodIndex === index ? { ...period, [field]: nextValue } : period
    ));
    updateDay(day, periods);
  };

  return (
    <div className="time-schedule-editor">
      {WEEKDAYS.map(({ key, label }) => (
        <div className="time-schedule-day" key={key}>
          <div className="time-schedule-day-header">
            <span className="time-schedule-day-label">{tr(label)}</span>
            <Button
              type="text"
              size="small"
              icon={<PlusOutlined />}
              onClick={() => updateDay(key, [...schedule[key], { start: '09:00', end: '18:00' }])}
            >
              {tr("添加")}
            </Button>
          </div>

          {schedule[key].length > 0 ? (
            <div className="time-schedule-periods">
              {schedule[key].map((period, index) => (
                <Space.Compact block key={`${key}-${index}`}>
                  <Input
                    type="time"
                    aria-label={trf("__VAR0__第 __VAR1__ 个时段开始时间", [tr(label), index + 1])}
                    value={period.start}
                    onChange={event => updatePeriod(key, index, 'start', event.target.value)}
                  />
                  <span className="time-schedule-separator">{tr("至")}</span>
                  <Input
                    type="time"
                    aria-label={trf("__VAR0__第 __VAR1__ 个时段结束时间", [tr(label), index + 1])}
                    value={period.end}
                    onChange={event => updatePeriod(key, index, 'end', event.target.value)}
                  />
                  <Button
                    danger
                    type="text"
                    icon={<DeleteOutlined />}
                    aria-label={trf("删除__VAR0__第 __VAR1__ 个时段", [tr(label), index + 1])}
                    onClick={() => updateDay(key, schedule[key].filter((_, itemIndex) => itemIndex !== index))}
                  />
                </Space.Compact>
              ))}
            </div>
          ) : (
            <div className="time-schedule-disabled">{tr("当天停用")}</div>
          )}
        </div>
      ))}
    </div>
  );
};

export default memo(TimeScheduleEditor);
