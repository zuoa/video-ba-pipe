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
| 401 | `api_key_required` | 缺少 `X-API-Key` 请求头 |
| 401 | `invalid_api_key` | API Key 无效或已禁用 |
| 404 | `video_source_not_found` | 视频源不存在 |
| 404 | `workflow_template_not_found` | 编排模板不存在 |
| 404 | `workflow_not_found` | 编排不存在 |
| 409 | `source_code_exists` | 视频源编码已存在 |

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
{ "source_url": "rtsp://admin:password@192.168.1.101:554/Streaming/Channels/101" }
```

**示例**

```bash
curl -X PUT http://<服务器地址>:5002/openapi/v1/video-sources/cam-gate-01/source-url \
  -H "X-API-Key: vbp_xxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{ "source_url": "rtsp://admin:password@192.168.1.101:554/Streaming/Channels/101" }'
```

**响应**

- `200`:地址未变化,或视频源未运行,已直接更新。
- `202`:地址已更新,运行中的视频源将异步重启解码器读取新地址。

```json
{
  "success": true,
  "data": {
    "source_code": "cam-gate-01",
    "source_url": "rtsp://admin:password@192.168.1.101:554/Streaming/Channels/101",
    "changed": true,
    "reload_scheduled": true
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
