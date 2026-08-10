# 多阶段级联检测

多阶段检测用于把多个 YOLO 兼容模型组合成一个可复用业务算法。例如：先检测人员，再仅在人员区域内检测烟，完整链条命中后输出“吸烟”。

## 配置规则

- 支持 2–8 个线性阶段。
- 第一阶段输入完整画面；后续阶段输入上一阶段检测框裁剪区域。
- 每阶段支持 Ultralytics、ONNX Runtime、RKNNLite 和共享 Ultralytics 推理。
- 后续阶段的局部检测框会映射回原图坐标。
- 最终框取第一阶段主体框，最终置信度取完整路径的最低置信度。
- 任一必需阶段整体失败时不产生业务检测；单个候选失败不会阻断其他候选。

```json
{
  "version": 1,
  "stages": [
    {
      "id": "person",
      "name": "找到人员",
      "model_id": 12,
      "class_ids": [0],
      "confidence": 0.6,
      "max_candidates": 20,
      "inference": {"backend": "auto", "nms_iou": 0.45},
      "input": {"type": "frame"}
    },
    {
      "id": "smoke",
      "name": "确认烟",
      "model_id": 27,
      "class_ids": [0],
      "confidence": 0.55,
      "max_candidates": 20,
      "inference": {"backend": "auto", "nms_iou": 0.45},
      "input": {
        "type": "parent_boxes",
        "parent_stage_id": "person",
        "expand_ratio": 0.1
      }
    }
  ],
  "output": {"label": "吸烟", "color": "#ff4d4f"}
}
```

## 管理与测试

在“算法管理 → 创建算法 → 多阶段检测”中按顺序添加阶段。高级设置默认折叠；模型元数据完整时可直接选择类别，否则可输入类别 ID。

保存前测试使用 `POST /api/algorithms/cascade/preview`，表单包含 `image` 文件和 JSON 字符串 `cascade_config`。响应中的 `stage_previews` 展示每阶段候选框、裁剪区域、耗时和状态。保存后的算法继续使用通用 `/api/algorithms/test` 接口。
