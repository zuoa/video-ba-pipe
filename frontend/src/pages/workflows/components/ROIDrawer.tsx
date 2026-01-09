import React, { useState, useRef, useEffect } from 'react';
import { Modal, Button, Space, message, Spin, Alert, Input, Select, List, Tag, Divider } from 'antd';
import {
  ClearOutlined,
  SaveOutlined,
  ReloadOutlined,
  InfoCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import { captureFrame } from '../../../services/api';
import './ROIDrawer.css';

const { TextArea } = Input;
const { Option } = Select;

export interface Point {
  x: number; // 相对坐标 0-1
  y: number; // 相对坐标 0-1
}

export interface ROIRegion {
  name: string;
  mode: 'pre_mask' | 'post_filter'; // 检测模式
  polygon: Point[]; // 多边形顶点（相对坐标）
}

export interface ROIDrawerProps {
  visible: boolean;
  videoSourceId: number | null;
  videoSourceName?: string;
  sourceCode?: string;
  initialRegions?: ROIRegion[]; // 多个 ROI 区域
  onClose: () => void;
  onSave: (regions: ROIRegion[]) => void; // 保存多个区域
}

const ROIDrawer: React.FC<ROIDrawerProps> = ({
  visible,
  videoSourceId,
  videoSourceName,
  sourceCode,
  initialRegions = [],
  onClose,
  onSave,
}) => {
  const [imageData, setImageData] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [currentPolygon, setCurrentPolygon] = useState<Point[]>([]); // 当前正在绘制的点（像素坐标）
  const [savedRegions, setSavedRegions] = useState<ROIRegion[]>(initialRegions); // 已保存的区域列表
  const [currentRegionName, setCurrentRegionName] = useState<string>(''); // 当前正在编辑的区域名称
  const [currentRegionMode, setCurrentRegionMode] = useState<'pre_mask' | 'post_filter'>('post_filter'); // 当前区域模式
  const [editingIndex, setEditingIndex] = useState<number>(-1); // -1 表示新建，>=0 表示编辑第几个区域
  const [imageSize, setImageSize] = useState<{ width: number; height: number } | null>(null);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imageRef = useRef<HTMLImageElement>(null);

  // 加载视频帧
  const loadFrame = async () => {
    if (!videoSourceId) {
      message.warning('请先选择视频源');
      return;
    }

    setLoading(true);
    try {
      const response = await captureFrame(videoSourceId);

      // 只有在实时截图失败时才尝试使用 snapshot
      if (response && response.success && response.image) {
        setImageData(response.image);
        message.success('视频帧加载成功');
        setLoading(false);
        return;
      }

      // 实时截图失败，尝试使用 snapshot
      console.log('实时截图失败，尝试使用快照');
      message.warning('无法获取实时帧，尝试使用快照');

      try {
        await loadSnapshotFrame();
      } catch (snapshotError) {
        console.error('加载快照也失败:', snapshotError);
        message.error('加载视频帧失败，请检查视频源是否正在运行');
      }
    } catch (error) {
      console.error('加载视频帧失败:', error);

      // 实时截图抛出异常，尝试使用 snapshot
      message.warning('加载实时帧失败，尝试使用快照');

      try {
        await loadSnapshotFrame();
      } catch (snapshotError) {
        console.error('加载快照也失败:', snapshotError);
        message.error('加载视频帧失败，请检查视频源是否正在运行');
      }
    } finally {
      setLoading(false);
    }
  };

  // 加载快照图片
  const loadSnapshotFrame = async () => {
    // 使用 sourceCode 构建快照 URL
    if (!sourceCode) {
      throw new Error('缺少 source_code');
    }

    const snapshotUrl = `/api/image/snapshots/${sourceCode}.jpg`;

    // 尝试加载图片
    const img = new Image();
    img.crossOrigin = 'anonymous';

    return new Promise<void>((resolve, reject) => {
      img.onload = () => {
        // 将图片转换为 base64
        const canvas = document.createElement('canvas');
        canvas.width = img.width;
        canvas.height = img.height;
        const ctx = canvas.getContext('2d');
        if (ctx) {
          ctx.drawImage(img, 0, 0);
          const base64 = canvas.toDataURL('image/jpeg');
          setImageData(base64);
          message.success('快照加载成功');
          resolve();
        } else {
          reject(new Error('无法创建 canvas'));
        }
      };
      img.onerror = () => {
        reject(new Error('图片加载失败'));
      };

      img.src = snapshotUrl;
    });
  };

  // 当模态框打开时加载帧
  useEffect(() => {
    if (visible && videoSourceId) {
      loadFrame();
      // 重置状态
      setCurrentPolygon([]);
      setSavedRegions(initialRegions);
      setCurrentRegionName('');
      setCurrentRegionMode('post_filter');
      setEditingIndex(-1);
    }
  }, [visible, videoSourceId]);

  // 图像加载完成后绘制
  useEffect(() => {
    if (imageData && imageRef.current) {
      imageRef.current.onload = () => {
        const width = imageRef.current!.width;
        const height = imageRef.current!.height;
        setImageSize({ width, height });
        drawCanvas();
      };
    }
  }, [imageData]);

  // 监听多边形变化，自动重绘
  useEffect(() => {
    if (imageData && canvasRef.current) {
      drawCanvas();
    }
  }, [savedRegions, currentPolygon, imageData]);

  // 绘制画布
  const drawCanvas = () => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx || !imageRef.current) return;

    // 设置画布尺寸匹配图像
    canvas.width = imageRef.current.width;
    canvas.height = imageRef.current.height;

    // 绘制图像
    ctx.drawImage(imageRef.current, 0, 0);

    // 绘制已保存的所有区域
    savedRegions.forEach((region, index) => {
      // 将相对坐标转换为像素坐标
      const pixelPoints = region.polygon.map(point => ({
        x: point.x * canvas.width,
        y: point.y * canvas.height,
      }));
      // 为不同区域使用不同颜色
      const colors = [
        { fill: 'rgba(24, 144, 255, 0.3)', stroke: 'rgba(24, 144, 255, 1)' },    // 蓝色
        { fill: 'rgba(82, 196, 26, 0.3)', stroke: 'rgba(82, 196, 26, 1)' },      // 绿色
        { fill: 'rgba(250, 173, 20, 0.3)', stroke: 'rgba(250, 173, 20, 1)' },    // 橙色
        { fill: 'rgba(191, 106, 255, 0.3)', stroke: 'rgba(191, 106, 255, 1)' },  // 紫色
        { fill: 'rgba(250, 84, 164, 0.3)', stroke: 'rgba(250, 84, 164, 1)' },    // 粉色
      ];
      const color = colors[index % colors.length];
      drawPolygon(ctx, pixelPoints, color.fill, color.stroke, false, region.name);
    });

    // 绘制当前正在绘制的多边形
    if (currentPolygon.length > 0) {
      drawPolygon(ctx, currentPolygon, 'rgba(255, 77, 79, 0.2)', 'rgba(255, 77, 79, 1)', true, currentRegionName || '新区域');
    }
  };

  // 绘制单个多边形
  const drawPolygon = (
    ctx: CanvasRenderingContext2D,
    polygon: Point[],
    fillColor: string,
    strokeColor: string,
    isIncomplete = false,
    label?: string
  ) => {
    if (polygon.length === 0) return;

    ctx.beginPath();
    ctx.moveTo(polygon[0].x, polygon[0].y);

    for (let i = 1; i < polygon.length; i++) {
      ctx.lineTo(polygon[i].x, polygon[i].y);
    }

    if (!isIncomplete) {
      ctx.closePath();
    }

    // 填充半透明底色
    if (!isIncomplete && polygon.length > 2) {
      ctx.fillStyle = fillColor;
      ctx.fill();
    }

    // 绘制边框
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = 3;
    ctx.stroke();

    // 绘制顶点
    polygon.forEach((point, index) => {
      ctx.beginPath();
      ctx.arc(point.x, point.y, 6, 0, Math.PI * 2);
      ctx.fillStyle = index === 0 ? '#52c41a' : '#ffffff';
      ctx.fill();
      ctx.strokeStyle = strokeColor;
      ctx.lineWidth = 2;
      ctx.stroke();
    });

    // 绘制标签（区域名称）
    if (label && polygon.length > 0) {
      // 计算多边形的中心点
      const centerX = polygon.reduce((sum, p) => sum + p.x, 0) / polygon.length;
      const centerY = polygon.reduce((sum, p) => sum + p.y, 0) / polygon.length;

      ctx.font = 'bold 16px Arial';
      const textMetrics = ctx.measureText(label);
      const padding = 6;

      // 绘制标签背景
      ctx.fillStyle = strokeColor;
      ctx.fillRect(
        centerX - textMetrics.width / 2 - padding,
        centerY - 12 - padding,
        textMetrics.width + padding * 2,
        24 + padding * 2
      );

      // 绘制标签文字
      ctx.fillStyle = '#ffffff';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(label, centerX, centerY);
    }
  };

  // 处理画布点击
  const handleCanvasClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;

    const x = (e.clientX - rect.left) * scaleX;
    const y = (e.clientY - rect.top) * scaleY;

    const newPoint: Point = { x, y };
    const newPolygon = [...currentPolygon, newPoint];

    // 检查是否点击了起点（闭合多边形）
    if (currentPolygon.length >= 3) {
      const startPoint = currentPolygon[0];
      const distance = Math.sqrt(
        Math.pow(x - startPoint.x, 2) + Math.pow(y - startPoint.y, 2)
      );
      if (distance < 15) {
        // 点击起点，闭合多边形
        completePolygon();
        return;
      }
    }

    setCurrentPolygon(newPolygon);
  };

  // 处理画布双击 - 自动完成多边形
  const handleCanvasDoubleClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    // 如果至少有3个点，则闭合多边形
    if (currentPolygon.length >= 3) {
      completePolygon();
    } else if (currentPolygon.length > 0) {
      message.warning('至少需要3个点才能构成多边形');
    }
  };

  // 完成多边形绘制
  const completePolygon = () => {
    const canvas = canvasRef.current;
    if (!canvas || !imageSize) return;

    if (currentRegionName.trim() === '') {
      message.warning('请先输入区域名称');
      return;
    }

    // 将像素坐标转换为相对坐标 (0-1)
    const normalizedPolygon = currentPolygon.map(point => ({
      x: point.x / canvas.width,
      y: point.y / canvas.height,
    }));

    const newRegion: ROIRegion = {
      name: currentRegionName,
      mode: currentRegionMode,
      polygon: normalizedPolygon,
    };

    if (editingIndex >= 0) {
      // 编辑模式：更新现有区域
      const updatedRegions = [...savedRegions];
      updatedRegions[editingIndex] = newRegion;
      setSavedRegions(updatedRegions);
      message.success(`ROI 区域 "${currentRegionName}" 已更新`);
    } else {
      // 新建模式：添加新区域
      setSavedRegions([...savedRegions, newRegion]);
      message.success(`ROI 区域 "${currentRegionName}" 已添加`);
    }

    // 重置当前绘制状态
    setCurrentPolygon([]);
    setCurrentRegionName('');
    setEditingIndex(-1);
  };

  // 清除当前绘制
  const handleClearCurrent = () => {
    setCurrentPolygon([]);
    setCurrentRegionName('');
    setEditingIndex(-1);
  };

  // 删除指定区域
  const handleDeleteRegion = (index: number) => {
    const updatedRegions = savedRegions.filter((_, i) => i !== index);
    setSavedRegions(updatedRegions);
    message.success(`ROI 区域 "${savedRegions[index].name}" 已删除`);
  };

  // 编辑指定区域
  const handleEditRegion = (index: number) => {
    const region = savedRegions[index];
    const canvas = canvasRef.current;
    if (!canvas || !imageSize) return;

    // 将相对坐标转换为像素坐标
    const pixelPolygon = region.polygon.map(point => ({
      x: point.x * canvas.width,
      y: point.y * canvas.height,
    }));

    setCurrentPolygon(pixelPolygon);
    setCurrentRegionName(region.name);
    setCurrentRegionMode(region.mode);
    setEditingIndex(index);
    message.info(`正在编辑区域 "${region.name}"`);
  };

  // 清除所有已保存的区域
  const handleClearAllRegions = () => {
    setSavedRegions([]);
    setCurrentPolygon([]);
    setCurrentRegionName('');
    setEditingIndex(-1);
    message.success('所有 ROI 区域已清除');
  };

  // 撤销最后一个点
  const handleUndoLastPoint = () => {
    if (currentPolygon.length > 0) {
      setCurrentPolygon(currentPolygon.slice(0, -1));
    }
  };

  // 保存所有 ROI 区域
  const handleSave = () => {
    if (savedRegions.length === 0) {
      message.warning('请先绘制至少一个 ROI 区域');
      return;
    }

    // 保存的是所有区域
    onSave(savedRegions);
    message.success(`已保存 ${savedRegions.length} 个 ROI 区域`);
    onClose();
  };

  return (
    <Modal
      title={
        <Space>
          <span>ROI 区域绘制</span>
          {videoSourceName && <span style={{ color: '#1890ff' }}>- {videoSourceName}</span>}
        </Space>
      }
      open={visible}
      onCancel={onClose}
      width="90vw"
      style={{ top: 20 }}
      footer={null}
    >
      <div className="roi-drawer-container">
        <div className="roi-controls">
          <Space direction="vertical" style={{ width: '100%' }}>
            <Button
              type="primary"
              icon={<ReloadOutlined />}
              onClick={loadFrame}
              loading={loading}
              block
            >
              重新加载帧
            </Button>

            <div className="control-divider" />

            <Alert
              message="操作说明"
              description={
                <ul style={{ margin: 0, paddingLeft: 20 }}>
                  <li>输入区域名称，选择检测模式</li>
                  <li>点击画布添加多边形顶点</li>
                  <li>至少需要 3 个点</li>
                  <li><strong>双击</strong>或<strong>点击起点</strong>完成当前区域</li>
                  <li>可以绘制多个区域，每个区域独立配置</li>
                </ul>
              }
              type="info"
              icon={<InfoCircleOutlined />}
              showIcon
            />

            <div className="control-divider" />

            {/* 当前区域配置 */}
            <div style={{ background: '#f5f5f5', padding: 12, borderRadius: 4 }}>
              <div style={{ marginBottom: 8, fontWeight: 'bold' }}>
                {editingIndex >= 0 ? '编辑区域' : '新区域配置'}
              </div>
              <Input
                placeholder="区域名称（如：大门、停车场）"
                value={currentRegionName}
                onChange={(e) => setCurrentRegionName(e.target.value)}
                style={{ marginBottom: 8 }}
                disabled={editingIndex >= 0}
              />
              <Select
                value={currentRegionMode}
                onChange={setCurrentRegionMode}
                style={{ width: '100%' }}
                disabled={editingIndex >= 0}
              >
                <Option value="pre_mask">前置掩码 (检测前屏蔽)</Option>
                <Option value="post_filter">后置过滤 (检测后过滤)</Option>
              </Select>
            </div>

            <div className="control-divider" />

            <Button
              icon={<ClearOutlined />}
              onClick={handleUndoLastPoint}
              disabled={currentPolygon.length === 0}
              block
            >
              撤销上一个点
            </Button>

            <Button
              icon={<ClearOutlined />}
              onClick={handleClearCurrent}
              disabled={currentPolygon.length === 0}
              block
            >
              清除当前绘制
            </Button>

            <Button
              danger
              icon={<ClearOutlined />}
              onClick={handleClearAllRegions}
              disabled={savedRegions.length === 0}
              block
            >
              清除所有区域
            </Button>

            <div className="control-divider" />

            {/* 已保存的区域列表 */}
            {savedRegions.length > 0 && (
              <>
                <div style={{ marginBottom: 8, fontWeight: 'bold' }}>
                  已保存区域 ({savedRegions.length})
                </div>
                <div style={{ maxHeight: 200, overflow: 'auto' }}>
                  {savedRegions.map((region, index) => (
                    <div
                      key={index}
                      style={{
                        background: editingIndex === index ? '#e6f7ff' : '#fafafa',
                        border: editingIndex === index ? '1px solid #1890ff' : '1px solid #d9d9d9',
                        borderRadius: 4,
                        padding: 8,
                        marginBottom: 8,
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div style={{ flex: 1 }}>
                          <div style={{ fontWeight: 'bold', fontSize: 13 }}>
                            {region.name}
                          </div>
                          <Tag size="small" color={region.mode === 'pre_mask' ? 'blue' : 'green'}>
                            {region.mode === 'pre_mask' ? '前置掩码' : '后置过滤'}
                          </Tag>
                          <div style={{ fontSize: 11, color: '#8c8c8c', marginTop: 4 }}>
                            {region.polygon.length} 个顶点
                          </div>
                        </div>
                        <Space size="small">
                          <Button
                            size="small"
                            icon={<EditOutlined />}
                            onClick={() => handleEditRegion(index)}
                            disabled={currentPolygon.length > 0}
                          >
                            编辑
                          </Button>
                          <Button
                            size="small"
                            danger
                            icon={<DeleteOutlined />}
                            onClick={() => handleDeleteRegion(index)}
                          >
                            删除
                          </Button>
                        </Space>
                      </div>
                    </div>
                  ))}
                </div>

                <div className="control-divider" />
              </>
            )}

            <div className="stats">
              <div>已保存区域: {savedRegions.length} 个</div>
              <div>当前顶点: {currentPolygon.length} 个</div>
            </div>

            <div className="control-divider" />

            <Button
              type="primary"
              icon={<SaveOutlined />}
              onClick={handleSave}
              disabled={savedRegions.length === 0}
              size="large"
              block
            >
              保存所有区域
            </Button>
          </Space>
        </div>

        <div className="roi-canvas-wrapper">
          {loading ? (
            <div className="canvas-loading">
              <Spin tip="加载视频帧中..." />
            </div>
          ) : imageData ? (
            <canvas
              ref={canvasRef}
              className="roi-canvas"
              onClick={handleCanvasClick}
              onDoubleClick={handleCanvasDoubleClick}
            />
          ) : (
            <div className="canvas-empty">
              <InfoCircleOutlined style={{ fontSize: 48, color: '#d9d9d9' }} />
              <p>点击"重新加载帧"按钮加载视频帧</p>
            </div>
          )}

          {/* 隐藏的 img 元素用于加载图像 */}
          <img ref={imageRef} src={imageData} style={{ display: 'none' }} alt="" />
        </div>
      </div>
    </Modal>
  );
};

export default ROIDrawer;
