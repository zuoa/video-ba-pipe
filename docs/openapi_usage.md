# Video BA Pipe 开放 API 使用说明

本文档介绍如何通过开放 API(`/openapi/v1`)与 Video BA Pipe 进行系统集成,完成视频源接入与算法编排的自动化管理。

配套的 OpenAPI 3.0 规范文件 `openapi.yaml` 可导入 Postman、Apifox、Swagger Editor 等工具直接调试。

---

## 1. 基本概念

| 概念 | 说明 |
| --- | --- |
| 视频源(Video Source) | 一路 RTSP/HTTP/本地文件视频流,通过 `source_code` 唯一标识 |
| 编排模板(Workflow Template) | 在 Web 界面中预先配置好的算法编排模板(`is_template = true`) |
| 派生编排(Workflow) | 由「视频源 + 模板」复制生成并激活的实际运行编排 |

典型集成流程:

```
添加视频源 → 查询编排模板 → 按模板激活编排 → (按需)更新流地址 / 去激活编排
```

## 2. 认证方式

所有 `/openapi/v1` 接口均通过请求头 `X-API-Key` 认证:

```
X-API-Key: vbp_xxxxxxxxxxxxxxxx
```

- API Key 由 **管理员** 在 Web 界面「系统设置 → API Key」中生成。
- 完整 Key 仅在生成时展示一次,请妥善保存。
- Key 缺失、无效或已禁用时,接口返回 `401`。
- 外部应用前端通过自己的应用服务器代理调用，API Key 保存在应用服务器端。

```bash
curl -H "X-API-Key: vbp_xxxxxxxxxxxxxxxx" \
  http://<服务器地址>:5002/openapi/v1/workflows
```

## 3. 通用约定

### 3.1 Base URL

```
http://<服务器地址>:5002/openapi/v1
```

### 3.2 请求与响应

- 请求体统一使用 `application/json`。
- 成功响应统一结构:

```json
{
  "success": true,
  "data": { }
}
```

- 失败响应统一结构:

```json
{
  "success": false,
  "code": "invalid_field",
  "message": "source_code 只能包含字母、数字、点、下划线、波浪号和连字符"
}
```

### 3.3 错误码

| HTTP 状态码 | code | 说明 |
| --- | --- | --- |
| 400 | `invalid_request` | 请求体不是合法 JSON 对象 |
| 400 | `missing_required_field` | 缺少必填字段 |
| 400 | `invalid_field` | 字段取值不合法 |
| 400 | `unknown_field` | 包含未定义的字段 |
| 400 | `field_not_allowed` | 字段不允许通过该接口修改 |
| 400 | `invalid_workflow_template` | 模板配置校验未通过 |
| 400 | `workflow_template_not_deactivatable` | 模板编排不能去激活 |
| 400 | `workflow_template_not_configurable` | 运行配置接口不能修改模板 |
| 400 | `invalid_workflow_structure` | 编排结构无效或插入点没有下游连接 |
| 401 | `api_key_required` | 缺少 `X-API-Key` 请求头 |
| 401 | `invalid_api_key` | API Key 无效或已禁用 |
| 404 | `video_source_not_found` | 视频源不存在 |
| 404 | `workflow_template_not_found` | 编排模板不存在 |
| 404 | `workflow_not_found` | 编排不存在 |
| 409 | `source_code_exists` | 视频源编码已存在 |
| 409 | `version_conflict` | 配置版本发生变化，需重新查询 |
| 409 | `entry_component_conflict` | 同类入口组件位于分支、存在多个实例或顺序异常 |

---

## 4. 接口详情

### 4.1 添加视频源

```
POST /openapi/v1/video-sources
```

**请求体**

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `source_code` | string | 是 | - | 视频源唯一编码,仅允许字母、数字、`.` `_` `~` `-`,最长 255 |
| `name` | string | 是 | - | 视频源名称 |
| `source_url` | string | 是 | - | 流地址(RTSP/HTTP-FLV/HLS/本地文件) |
| `enabled` | boolean | 否 | `true` | 是否启用 |
| `source_decode_width` | integer | 否 | `640` | 解码宽度 |
| `source_decode_height` | integer | 否 | `360` | 解码高度 |
| `source_fps` | integer | 否 | `5` | 解码帧率 |
| `source_codec` | string | 否 | `unknown` | 编码格式:`unknown` / `h264` / `h265` |
| `decode_keyframes_only` | boolean / null | 否 | `null` | `null` 继承系统设置；系统默认关闭 |

**示例**

```bash
curl -X POST http://<服务器地址>:5002/openapi/v1/video-sources \
  -H "X-API-Key: vbp_xxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "source_code": "cam-gate-01",
    "name": "东门相机",
    "source_url": "rtsp://admin:password@192.168.1.100:554/Streaming/Channels/101",
    "source_decode_width": 640,
    "source_decode_height": 360,
    "source_fps": 5
  }'
```

**响应 `201`**

```json
{
  "success": true,
  "data": {
    "id": 12,
    "name": "东门相机",
    "enabled": true,
    "source_code": "cam-gate-01",
    "source_url": "rtsp://admin:password@192.168.1.100:554/Streaming/Channels/101",
    "source_decode_width": 640,
    "source_decode_height": 360,
    "source_fps": 5,
    "source_codec": "unknown",
    "decode_keyframes_only": null,
    "status": "STOPPED"
  }
}
```

`source_code` 已存在时返回 `409`。

### 4.2 编辑视频源

```
PATCH /openapi/v1/video-sources/{source_code}
```

仅允许修改 `name`、`enabled`、`source_decode_width`、`source_decode_height`、`source_fps`、`source_codec`、`decode_keyframes_only`;`source_code` 与 `source_url` 不可通过本接口修改。

**示例**

```bash
curl -X PATCH http://<服务器地址>:5002/openapi/v1/video-sources/cam-gate-01 \
  -H "X-API-Key: vbp_xxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{ "name": "东门相机(高清)", "source_fps": 15 }'
```

**响应 `200`**:返回更新后的视频源对象,结构同 4.1。

### 4.3 更新视频源地址

```
PUT /openapi/v1/video-sources/{source_code}/source-url
```

**请求体**

```json
{
  "source_url": "rtsp://admin:password@192.168.1.101:554/Streaming/Channels/101",
  "switch_stream_immediately": false
}
```

- `switch_stream_immediately` 可选，默认为 `false`，当前流有效时不会重复启停。
- 设为 `false` 时，运行中的解码器继续使用旧地址；旧流发生断流、持续无帧或接流失败后，系统再启用新地址。
- 视频源未运行时无需等待，下一次启动会直接使用新地址。

**示例**

```bash
curl -X PUT http://<服务器地址>:5002/openapi/v1/video-sources/cam-gate-01/source-url \
  -H "X-API-Key: vbp_xxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{ "source_url": "rtsp://admin:password@192.168.1.101:554/Streaming/Channels/101", "switch_stream_immediately": false }'
```

**响应**

- `200`:地址未变化,或视频源未运行,已直接更新。
- `202`:地址已更新；运行中的视频源已安排立即重载或失效后切换，具体见响应字段。

```json
{
  "success": true,
  "data": {
    "source_code": "cam-gate-01",
    "source_url": "rtsp://admin:password@192.168.1.101:554/Streaming/Channels/101",
    "changed": true,
    "switch_stream_immediately": false,
    "stream_switch": "deferred",
    "reload_scheduled": false,
    "switch_deferred": true
  }
}
```

### 4.4 查询编排模板

```
GET /openapi/v1/workflow-templates
```

**示例**

```bash
curl -H "X-API-Key: vbp_xxxxxxxxxxxxxxxx" \
  http://<服务器地址>:5002/openapi/v1/workflow-templates
```

**响应 `200`**

```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": 3,
        "name": "人员检测模板",
        "description": "标准人员检测编排",
        "workflow_data": { "nodes": [], "connections": [] },
        "is_active": false,
        "is_template": true,
        "source_template_id": null,
        "source_template_name": null,
        "video_source_id": null,
        "source_code": null,
        "config_version": 1,
        "created_at": "2026-01-01T10:00:00",
        "updated_at": "2026-01-02T10:00:00"
      }
    ],
    "total": 1
  }
}
```

返回项中的 `id` 即为激活接口所需的 `template_workflow_id`。

### 4.5 激活编排(按模板复制)

```
POST /openapi/v1/workflow-activations
```

按「视频源 + 模板」复制生成派生编排并激活。相同视频源和模板的重复请求会复用并激活已有派生编排(幂等)。

**请求体**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `source_code` | string | 是 | 视频源编码 |
| `template_workflow_id` | integer | 是 | 编排模板 ID(由 4.4 获取) |

**示例**

```bash
curl -X POST http://<服务器地址>:5002/openapi/v1/workflow-activations \
  -H "X-API-Key: vbp_xxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{ "source_code": "cam-gate-01", "template_workflow_id": 3 }'
```

**响应**

- `201`:派生编排已创建并激活。
- `200`:已有派生编排被复用并激活。

```json
{
  "success": true,
  "data": {
    "workflow_id": 25,
    "template_workflow_id": 3,
    "source_code": "cam-gate-01",
    "created": true,
    "is_active": true
  }
}
```

### 4.6 查询派生编排

```
GET /openapi/v1/workflows?source_code=<可选>
```

**查询参数**

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `source_code` | 否 | 按视频源编码过滤;不传时返回全部非模板编排 |

**示例**

```bash
curl -H "X-API-Key: vbp_xxxxxxxxxxxxxxxx" \
  "http://<服务器地址>:5002/openapi/v1/workflows?source_code=cam-gate-01"
```

**响应 `200`**:结构同 4.4,`items` 为派生编排列表。

### 4.7 去激活编排

```
POST /openapi/v1/workflows/{workflow_id}/deactivate
```

**示例**

```bash
curl -X POST http://<服务器地址>:5002/openapi/v1/workflows/25/deactivate \
  -H "X-API-Key: vbp_xxxxxxxxxxxxxxxx"
```

**响应 `200`**

```json
{
  "success": true,
  "data": { "workflow_id": 25, "is_active": false }
}
```

模板编排不能去激活,将返回 `400 workflow_template_not_deactivatable`。

---

### 4.8 查询运行编排配置

```
GET /openapi/v1/workflows/{workflow_id}/configuration
```

先使用现有编排列表接口选择运行编排，再查询配置。时间与 ROI 通过能力接口配置，无需提供 `node_id`；公共参数批量修改使用本接口返回的各编排节点 ID。

```json
{
  "success": true,
  "data": {
    "workflow_id": 25,
    "config_version": 3,
    "is_active": true,
    "time_schedule": {
      "status": "missing",
      "node_id": null,
      "weekly_schedule": null
    },
    "roi_draw": {
      "status": "missing",
      "node_id": null,
      "roi_regions": null
    },
    "common_parameters": [
      {
        "node_id": "algorithm-001",
        "node_type": "algorithm",
        "name": "人员检测",
        "allowed_fields": ["confidence", "interval_seconds"],
        "config": {"confidence": 0.5, "interval_seconds": 1}
      }
    ]
  }
}
```

入口状态：`configured` 表示可直接修改，`missing` 表示提交时按需补建，`conflict` 表示须先在编辑器中整理结构。公共参数未显式配置的值返回 `null`，不表示运行时有效值为零。

### 4.9 设置每周启用时段

```
PUT /openapi/v1/workflows/{workflow_id}/configuration/time-schedule
```

```json
{
  "expected_version": 3,
  "dry_run": true,
  "weekly_schedule": {
    "1": [{"start": "08:00", "end": "18:00"}],
    "2": [{"start": "08:00", "end": "18:00"}],
    "3": [{"start": "08:00", "end": "18:00"}],
    "4": [{"start": "08:00", "end": "18:00"}],
    "5": [{"start": "08:00", "end": "18:00"}],
    "6": [],
    "7": []
  }
}
```

- 星期 `1` 为周一，`7` 为周日，七天都须提供；每天可设置多个时段。
- 按执行服务器本地时区判断，开始和结束分钟均包含在内；全天使用 `00:00` 至 `23:59`。
- 跨日时段必须拆成两天，例如周一 `22:00–23:59` 加周二 `00:00–02:00`。
- 当天空数组表示当天不启用，全周至少一个启用时段；不提供额外的时间组件启停开关。

### 4.10 设置 ROI 区域

```
PUT /openapi/v1/workflows/{workflow_id}/configuration/roi
```

```json
{
  "expected_version": 3,
  "dry_run": true,
  "roi_regions": [
    {
      "name": "入口检测区",
      "mode": "post_filter",
      "anchor": "bottom_center",
      "polygon": [
        {"x": 0.1, "y": 0.2},
        {"x": 0.9, "y": 0.2},
        {"x": 0.9, "y": 0.8},
        {"x": 0.1, "y": 0.8}
      ]
    }
  ]
}
```

- 每次整体替换区域列表；支持多个区域。
- 顶点为相对原始视频画面的归一化坐标，范围 `[0,1]`，至少三个不同顶点且面积非零。前端绘制时需先扣除画面留白，再按实际画面宽高换算坐标。
- 检测模式支持 `pre_mask`、`crop_infer`、`post_filter`。
- `anchor` 可选，支持目标框九个判定点，详见 OpenAPI 枚举；省略时沿用算法原有规则。
- `roi_regions: []` 清除已有区域，不删除组件；组件不存在时不创建空组件，版本保持不变。清除入口 ROI 后，下游算法恢复其原有 ROI 配置或默认规则。

### 4.11 入口补建、预检与生效

时间与 ROI 作用于当前编排的全部视频源下游分支，入口固定为：

```
视频源 → [时间启动区间] → [热区绘制] → 原有下游流程
```

只配置一项时只补建对应组件，另一项后续补建仍遵循上述顺序。原有分支、目标端口、连线条件和标签保持原意。同类组件位于分支、存在多个实例或顺序异常时返回 `409 entry_component_conflict`，不会自动移动或合并。插入点没有下游连接时返回 `400 invalid_workflow_structure`。

两个写接口的 `dry_run` 默认为 `true`，预检不保存、不递增版本、不重载。预检响应中的 `change.action` 为 `created`、`updated` 或 `unchanged`，`change.connections` 包含连线修改前后内容。预检中的新节点 ID 仅供展示，正式提交时重新生成。

检查预检结果后，以相同配置和版本重新请求并设置 `dry_run: false`。补建、接线及参数保存原子提交，每个编排版本递增一次；响应的 `data.config_version` 为提交后的版本。连续设置时间和 ROI 时，第二次请求使用第一次提交返回的新版本。

成功表示配置已保存。已激活编排由 Worker 异步重载，未激活编排下次启动时使用新配置。重载单位为视频源执行进程，因此同一视频源下的其他运行编排也可能短暂中断。

`409 version_conflict` 时重新查询配置并让用户核对变更，再预检和提交；不要直接换成新版本号自动覆盖。配置错误响应的 `details` 包含编排、节点或字段定位信息。模板不允许通过这些接口修改。

### 4.12 批量修改公共参数

```
POST /openapi/v1/workflows/batch-config
```

```json
{
  "workflow_ids": [25, 26],
  "expected_versions": {"25": 4, "26": 2},
  "dry_run": true,
  "targets": [
    {
      "workflow_ids": [25],
      "node_id": "algorithm-001",
      "node_type": "algorithm",
      "changes": {"confidence": 0.7, "interval_seconds": 2}
    },
    {
      "workflow_ids": [26],
      "node_id": "algorithm-009",
      "node_type": "algorithm",
      "changes": {"confidence": 0.7, "interval_seconds": 2}
    }
  ]
}
```

| 节点类型 | 公共参数 | 约束 |
| --- | --- | --- |
| `algorithm` | `confidence`、`interval_seconds` | 置信度 0–1，检测间隔 0.1–60 秒；设置置信度会启用节点覆盖 |
| `alert` | `trigger_condition`、`suppression` | 检测窗口 1–300；次数阈值 1–100，比例阈值 0–1；抑制时长 1–3600 秒 |
| `time_schedule` | `weekly_schedule` | 与单独设置周时段的规则一致 |

告警配置示例：

```json
{
  "trigger_condition": {"enable": true, "mode": "count", "window_size": 10, "threshold": 3},
  "suppression": {"enable": true, "seconds": 60}
}
```

`trigger_condition.mode` 支持 `count`、`ratio`、`consecutive`，停用某项时传 `{"enable": false}`。

仅覆盖明确提交的公共参数；嵌套的告警窗口或抑制对象按整体配置处理。同一编排同一节点只能指定一次，每个已选编排必须有目标；`expected_versions` 必须与已选编排一一对应。跨编排不假定节点 ID 相同，应分别查询并选择。

批量操作只修改已有组件，不补建算法、告警或时间节点；缺少时间节点时先调用专用时间接口。ROI 使用专用接口配置。

预检返回 `summary`、`changes`、当前 `config_versions`；正式提交返回新 `config_versions`。任一目标校验失败或发生版本冲突，整批不写入。同一编排修改多个节点只递增一次版本。

## 5. 典型集成示例(Python)

```python
import requests

BASE_URL = "http://192.168.1.10:5002/openapi/v1"
HEADERS = {"X-API-Key": "vbp_xxxxxxxxxxxxxxxx"}

# 1. 添加视频源
resp = requests.post(f"{BASE_URL}/video-sources", headers=HEADERS, json={
    "source_code": "cam-gate-01",
    "name": "东门相机",
    "source_url": "rtsp://admin:password@192.168.1.100:554/Streaming/Channels/101",
})
resp.raise_for_status()

# 2. 查询模板,取第一个模板
templates = requests.get(f"{BASE_URL}/workflow-templates", headers=HEADERS).json()
template_id = templates["data"]["items"][0]["id"]

# 3. 激活编排
resp = requests.post(f"{BASE_URL}/workflow-activations", headers=HEADERS, json={
    "source_code": "cam-gate-01",
    "template_workflow_id": template_id,
})
workflow_id = resp.json()["data"]["workflow_id"]
print("已激活编排:", workflow_id)

# 4. 需要时去激活
requests.post(f"{BASE_URL}/workflows/{workflow_id}/deactivate", headers=HEADERS)
```

## 6. 常见问题

**Q: 收到 `401 invalid_api_key`?**
检查 `X-API-Key` 请求头是否完整(以 `vbp_` 开头)、Key 是否已在系统设置中被禁用。

**Q: 激活编排返回 `400 invalid_workflow_template`?**
模板缺少合法的源节点或时段配置,请先在 Web 界面的编排编辑器中修正模板。

**Q: 更新流地址后多久生效?**
运行中的视频源返回 `202` 后会异步重启解码器,通常在数秒内切换到新地址;未运行的视频源在下次启动时使用新地址。
