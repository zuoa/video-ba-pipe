import { AimOutlined, CheckCircleFilled } from '@ant-design/icons';
import './DetectionResponseContract.css';

const RESPONSE_EXAMPLE = `{
  "has_detection": true,
  "detections": [
    {
      "bbox": [120, 80, 420, 360],
      "label": "person",
      "confidence": 0.93
    }
  ],
  "metadata": {}
}`;

export default function DetectionResponseContract() {
  return (
    <section className="detection-response-contract" aria-label="告警标注框对接要求">
      <div className="detection-response-contract__heading">
        <span className="detection-response-contract__icon" aria-hidden="true">
          <AimOutlined />
        </span>
        <div>
          <div className="detection-response-contract__title">告警标注框对接要求</div>
          <div className="detection-response-contract__intro">
            <code>has_detection</code> 只决定是否命中；要在测试结果和告警图片中画框，还需返回目标坐标。
          </div>
        </div>
      </div>

      <div className="detection-response-contract__body">
        <ul className="detection-response-contract__requirements">
          <li>
            <CheckCircleFilled aria-hidden="true" />
            <span>工作流节点使用<strong>同步等待</strong>；异步提交不会读取接口响应。</span>
          </li>
          <li>
            <CheckCircleFilled aria-hidden="true" />
            <span><code>detections_path</code> 必须指向目标数组，例如 <code>data.detections</code>。</span>
          </li>
          <li>
            <CheckCircleFilled aria-hidden="true" />
            <span>每个目标使用原图坐标 <code>[x1, y1, x2, y2]</code>；支持像素值或 0～1 归一化值。</span>
          </li>
        </ul>

        <div className="detection-response-contract__example">
          <div className="detection-response-contract__example-label">最小可画框响应</div>
          <pre>{RESPONSE_EXAMPLE}</pre>
        </div>
      </div>

      <div className="detection-response-contract__aliases">
        坐标字段兼容 <code>box</code>、<code>bbox</code>、<code>xyxy</code>；标签兼容 <code>label</code>、<code>label_name</code>、<code>class_name</code>；置信度兼容 <code>confidence</code>、<code>score</code>。接口内部如缩放图片，返回前需换算回输入原图坐标。
      </div>
    </section>
  );
}
