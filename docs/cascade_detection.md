# 组合检测

组合检测把模型执行的数据流与业务判定规则分开配置。它既支持原来的线性正向级联，也支持“检测到主体，但没有检测到另一目标才输出”等反向规则。

## 未戴安全帽示例

在“算法管理 → 配置向导 → 组合检测”中选择“未检测到目标模板”：

1. `画面输入 → 检测头部 → 检测安全帽` 使用蓝色数据线连接。安全帽模型只处理对应头部的裁剪区域。
2. `检测头部 → 存在`、`检测安全帽 → 不存在` 使用橙色判定线连接，再通过 AND 连接最终输出。
3. 判定范围选择“逐主体”，锚点选择“检测头部”。多人画面会分别判断每个头部，不会让其他人的安全帽抵消未戴帽目标。
4. 输出框来源选择“检测头部”，标签填写“未戴安全帽”。
5. 建议启用时间窗口检测，抑制单帧漏检造成的误报。

模型正常执行并返回 0 个目标时，“不存在”才成立。模型超时、后端不可用或裁剪推理失败会产生 `unknown`，不会被 NOT 转换为告警。

## v2 图配置

```json
{
  "version": 2,
  "evaluation": {"scope": "per_anchor", "anchor_node_id": "head"},
  "nodes": [
    {"id": "frame", "type": "frame", "name": "画面输入"},
    {
      "id": "head",
      "type": "detector",
      "name": "检测头部",
      "model_id": 12,
      "class_ids": [0],
      "confidence": 0.6,
      "max_candidates": 20,
      "expand_ratio": 0.1,
      "inference": {"backend": "auto", "nms_iou": 0.45}
    },
    {
      "id": "helmet",
      "type": "detector",
      "name": "检测安全帽",
      "model_id": 27,
      "class_ids": [0],
      "confidence": 0.55,
      "max_candidates": 20,
      "expand_ratio": 0.1,
      "inference": {"backend": "auto", "nms_iou": 0.45}
    },
    {"id": "head_exists", "type": "predicate", "name": "检测到头部", "operator": "exists"},
    {"id": "helmet_missing", "type": "predicate", "name": "没有安全帽", "operator": "not_exists"},
    {"id": "all", "type": "logic", "name": "全部满足", "operator": "and"},
    {
      "id": "output",
      "type": "output",
      "name": "最终输出",
      "label": "未戴安全帽",
      "color": "#ff4d4f",
      "box_source_node_id": "head"
    }
  ],
  "edges": [
    {"source": "frame", "target": "head", "kind": "data"},
    {"source": "head", "target": "helmet", "kind": "data"},
    {"source": "head", "target": "head_exists", "kind": "rule"},
    {"source": "helmet", "target": "helmet_missing", "kind": "rule"},
    {"source": "head_exists", "target": "all", "kind": "rule"},
    {"source": "helmet_missing", "target": "all", "kind": "rule"},
    {"source": "all", "target": "output", "kind": "rule"}
  ],
  "layout": {"nodes": {}}
}
```

### 节点和规则

- `frame`：完整画面输入，只允许一个。
- `detector`：YOLO、ONNX 或 RKNN 检测模型；最多 8 个。每个检测节点只能有一个数据输入。
- `predicate`：支持 `exists`、`not_exists`、`eq`、`ne`、`gt`、`gte`、`lt` 和 `lte`。
- `logic`：支持 `and`、`or` 和单输入 `not`，可以嵌套组合。
- `output`：只允许一个；可选择检测节点作为画框来源，也可输出无框业务事件。
- `evaluation.scope`：`per_anchor` 表示逐主体判断，`frame` 表示整帧汇总判断。

数据流和规则流都必须无环，所有判定节点必须最终连接到输出节点。

## 管理与测试

保存前测试使用 `POST /api/algorithms/cascade/preview`，表单包含 `image` 文件和 JSON 字符串 `cascade_config`。响应包含：

- `node_previews`：检测节点的输入数、命中数、裁剪区域、状态和耗时。
- `context_evaluations`：每个主体的条件计数、真假或未知状态以及最终结果。
- `diagnosis`：本次测试的总体结论，以及最先中断的检测节点。
- `result_image`：最终业务输出图。

每个检测节点会进一步返回 `execution_state` 和 `reason`，用于区分：

- `matched`：模型已执行并检出目标。
- `not_matched`：模型已执行，但没有检出目标。
- `skipped`：上游没有目标，因此本节点没有执行。
- `blocked`：上游执行异常，因此本节点不能执行。
- `degraded`：部分裁剪推理成功、部分失败，结果不完整。
- `failed`：本节点推理失败。

测试页同时展示输入候选数、成功执行次数、命中数、下传数和截断数。逐主体判定中的每个条件也会说明它读取了哪个检测节点，以及为什么成立、不成立或无法判断。

接口暂时继续返回 `stage_previews`，用于兼容旧页面。保存后的算法继续使用通用 `/api/algorithms/test` 接口。

## v1 兼容

已有 `version: 1` 的线性级联配置继续按原逻辑运行。进入编辑页时，前端会将其转换为等价的 v2 检测节点、存在条件和 AND 规则；再次保存后使用 v2 图配置。
