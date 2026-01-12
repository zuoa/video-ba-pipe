import React from 'react';
import {Card, Tag, Badge, Space, Typography, Tooltip} from 'antd';
import {
    UserOutlined,
    MobileOutlined,
    WarningOutlined,
    InfoCircleOutlined,
    CloseCircleOutlined,
    ExclamationCircleOutlined,
    VideoCameraOutlined,
    ClockCircleOutlined,
    PlayCircleOutlined,
    ApartmentOutlined,
    FireOutlined
} from '@ant-design/icons';
import {Alert, Task} from '../types';
import RelativeTime from './RelativeTime';

const {Text, Title} = Typography;

interface AlertCardProps {
    alert: Alert;
    task?: Task;
    onClick?: () => void;
}

// 告警类型图标映射
const ALERT_ICONS: Record<string, React.ReactNode> = {
    warning: <WarningOutlined/>,
    error: <CloseCircleOutlined/>,
    info: <InfoCircleOutlined/>,
    critical: <FireOutlined/>,
    person_detection: <UserOutlined/>,
    phone_detection_2stage: <MobileOutlined/>,
};

// 告警类型颜色映射 - 使用渐变色
const ALERT_COLORS: Record<string, { primary: string; gradient: string; bg: string }> = {
    warning: {
        primary: '#faad14',
        gradient: 'linear-gradient(135deg, #faad14 0%, #ffc53d 100%)',
        bg: '#fff7e6',
    },
    error: {
        primary: '#ff4d4f',
        gradient: 'linear-gradient(135deg, #ff4d4f 0%, #ff7875 100%)',
        bg: '#fff1f0',
    },
    info: {
        primary: '#1677ff',
        gradient: 'linear-gradient(135deg, #1677ff 0%, #4096ff 100%)',
        bg: '#e6f4ff',
    },
    critical: {
        primary: '#722ed1',
        gradient: 'linear-gradient(135deg, #722ed1 0%, #9254de 100%)',
        bg: '#f9f0ff',
    },
    person_detection: {
        primary: '#1677ff',
        gradient: 'linear-gradient(135deg, #1677ff 0%, #4096ff 100%)',
        bg: '#e6f4ff',
    },
    phone_detection_2stage: {
        primary: '#faad14',
        gradient: 'linear-gradient(135deg, #faad14 0%, #ffc53d 100%)',
        bg: '#fff7e6',
    },
};

// 获取默认颜色
const DEFAULT_COLOR = {
    primary: '#1677ff',
    gradient: 'linear-gradient(135deg, #1677ff 0%, #4096ff 100%)',
    bg: '#e6f4ff',
};

const AlertCard: React.FC<AlertCardProps> = ({alert, task, onClick}) => {
    const taskName = task?.name || `任务 #${alert.task_id}`;
    const alertIcon = ALERT_ICONS[alert.alert_type] || <InfoCircleOutlined/>;
    const colorScheme = ALERT_COLORS[alert.alert_type] || DEFAULT_COLOR;
    const isCritical = alert.alert_type === 'critical' || alert.alert_type === 'error';

    return (
        <Card
            hoverable
            onClick={onClick}
            style={{
                borderRadius: 12,
                overflow: 'hidden',
                border: `1px solid ${colorScheme.primary}20`,
                boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
                transition: 'all 0.3s ease',
            }}
            styles={{
                body: {
                    padding: '16px',
                    background: isCritical ? `${colorScheme.bg}40` : '#fff',
                },
            }}
            classNames={{
                body: 'alert-card-body',
            }}
            cover={
                <div
                    style={{
                        position: 'relative',
                        aspectRatio: 16 / 9,
                        background: `linear-gradient(135deg, ${colorScheme.bg} 0%, #ffffff 100%)`,
                        overflow: 'hidden',
                    }}
                >
                    {/* 背景装饰 */}
                    <div
                        style={{
                            position: 'absolute',
                            top: 0,
                            left: 0,
                            right: 0,
                            bottom: 0,
                            background: colorScheme.gradient,
                            opacity: 0.05,
                        }}
                    />

                    {alert.alert_image ? (
                        <img
                            alt="alert"
                            src={`/api/image/frames/${alert.alert_image}`}
                            style={{
                                width: '100%',
                                height: '100%',
                                objectFit: 'cover',
                                transition: 'transform 0.3s ease',
                            }}
                        />
                    ) : (
                        <div
                            style={{
                                width: '100%',
                                height: '100%',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                fontSize: 56,
                                color: colorScheme.primary,
                                background: `linear-gradient(135deg, ${colorScheme.bg} 0%, #ffffff 100%)`,
                            }}
                        >
                            {alertIcon}
                        </div>
                    )}

                    {/* 顶部状态栏 */}
                    <div
                        style={{
                            position: 'absolute',
                            top: 0,
                            left: 0,
                            right: 0,
                            padding: '12px',
                            background: 'linear-gradient(to bottom, rgba(0,0,0,0.4) 0%, transparent 100%)',
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'flex-start',
                        }}
                    >
                        {/* 告警类型标签 */}
                        <div
                            style={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: 6,
                                padding: '4px 12px',
                                borderRadius: 20,
                                background: colorScheme.gradient,
                                color: '#fff',
                                fontSize: 12,
                                fontWeight: 600,
                                boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
                            }}
                        >
                            {alertIcon}
                            <span style={{textTransform: 'capitalize'}}>
                                {alert.alert_type.replace(/_/g, ' ')}
                            </span>
                        </div>

                        {/* 工作流信息 */}
                        {alert.workflow_id && (
                            <Tooltip
                                title={alert.workflow_name || `流程编排 #${alert.workflow_id}`}
                            >
                                <div
                                    style={{
                                        display: 'flex',
                                        alignItems: 'center',
                                        gap: 4,
                                        padding: '4px 10px',
                                        borderRadius: 12,
                                        background: 'rgba(255,255,255,0.95)',
                                        backdropFilter: 'blur(10px)',
                                        fontSize: 11,
                                        color: '#666',
                                        fontWeight: 500,
                                    }}
                                >
                                    <ApartmentOutlined style={{fontSize: 10}}/>
                                    <span style={{maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'}}>
                                        {alert.workflow_name || `#${alert.workflow_id}`}
                                    </span>
                                </div>
                            </Tooltip>
                        )}
                    </div>

                    {/* 检测帧数徽章 */}
                    {alert.detection_count > 1 && (
                        <Tooltip title={`检测 ${alert.detection_count} 帧`}>
                            <div
                                style={{
                                    position: 'absolute',
                                    bottom: 12,
                                    right: 12,
                                    display: 'flex',
                                    alignItems: 'center',
                                    justifyContent: 'center',
                                    width: 32,
                                    height: 32,
                                    borderRadius: '50%',
                                    background: colorScheme.gradient,
                                    color: '#fff',
                                    fontSize: 13,
                                    fontWeight: 'bold',
                                    boxShadow: '0 2px 8px rgba(0,0,0,0.25)',
                                    border: '2px solid #fff',
                                }}
                            >
                                {alert.detection_count}
                            </div>
                        </Tooltip>
                    )}

                    {/* 视频录制标识 */}
                    {alert.alert_video && (
                        <div
                            style={{
                                position: 'absolute',
                                bottom: 12,
                                left: 12,
                                display: 'flex',
                                alignItems: 'center',
                                gap: 4,
                                padding: '4px 10px',
                                borderRadius: 12,
                                background: 'rgba(22, 119, 255, 0.95)',
                                backdropFilter: 'blur(10px)',
                                color: '#fff',
                                fontSize: 11,
                                fontWeight: 500,
                                boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
                            }}
                        >
                            <PlayCircleOutlined style={{fontSize: 12}}/>
                            <span>录像</span>
                        </div>
                    )}
                </div>
            }
        >
            <Space direction="vertical" style={{width: '100%'}} size={12}>
                {/* 任务名称 */}
                <div
                    style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                    }}
                >
                    <div
                        style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            width: 28,
                            height: 28,
                            borderRadius: 8,
                            background: colorScheme.bg,
                            color: colorScheme.primary,
                        }}
                    >
                        <VideoCameraOutlined style={{fontSize: 14}}/>
                    </div>
                    <Text
                        strong
                        ellipsis
                        style={{fontSize: 14, color: '#262626', flex: 1}}
                    >
                        {taskName}
                    </Text>
                </div>

                {/* 告警消息 */}
                <Tooltip title={<div style={{ whiteSpace: 'pre-wrap' }}>{alert.alert_message}</div>}>
                    <div
                        style={{
                            fontSize: 13,
                            color: '#595959',
                            lineHeight: 1.6,
                            padding: '10px 12px',
                            background: `${colorScheme.bg}60`,
                            borderRadius: 8,
                            border: `1px solid ${colorScheme.primary}15`,
                            display: '-webkit-box',
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: 'vertical',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                        }}
                    >
                        {alert.alert_message}
                    </div>
                </Tooltip>

                {/* 底部信息栏 */}
                <div
                    style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        paddingTop: 4,
                        borderTop: `1px solid ${colorScheme.primary}10`,
                    }}
                >
                    <Space size={6}>
                        <ClockCircleOutlined
                            style={{
                                fontSize: 13,
                                color: colorScheme.primary,
                            }}
                        />
                        <Text
                            style={{
                                fontSize: 12,
                                color: '#8c8c8c',
                                fontWeight: 500,
                            }}
                        >
                            <RelativeTime time={alert.alert_time} showFullTime/>
                        </Text>
                    </Space>

                    {/* 告警级别标识 */}
                    {isCritical && (
                        <div
                            style={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: 4,
                                fontSize: 11,
                                color: colorScheme.primary,
                                fontWeight: 600,
                                padding: '2px 8px',
                                borderRadius: 4,
                                background: colorScheme.bg,
                            }}
                        >
                            <ShieldCheckOutlined style={{fontSize: 11}}/>
                            <span>已确认</span>
                        </div>
                    )}
                </div>
            </Space>
        </Card>
    );
};

export default AlertCard;
