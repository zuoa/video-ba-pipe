# Video BA Pipe 功能操作手册

> 版本：2026-08 ｜ 适用版本：当前主线（main）
> 本文档面向系统使用者与运维人员，介绍 Video BA Pipe 视频智能分析系统的各项功能及操作方法。

---

## 目录

1. [系统简介](#1-系统简介)
2. [快速上手](#2-快速上手)
3. [登录与用户权限](#3-登录与用户权限)
4. [仪表板](#4-仪表板)
5. [视频源管理](#5-视频源管理)
6. [模型管理](#6-模型管理)
7. [算法管理](#7-算法管理)
8. [脚本管理](#8-脚本管理)
9. [工作流编排](#9-工作流编排)
10. [工作流节点详解](#10-工作流节点详解)
11. [告警管理](#11-告警管理)
12. [外部 API 管理](#12-外部-api-管理)
13. [系统设置](#13-系统设置)
14. [对外 OpenAPI 集成](#14-对外-openapi-集成)
15. [部署方式](#15-部署方式)
16. [常见问题排查](#16-常见问题排查)

---

## 1. 系统简介

Video BA Pipe 是一套视频流智能分析系统，支持接入 RTSP / HTTP-FLV / HLS / 本地视频文件等多种视频源，通过**可视化节点工作流**灵活编排 AI 检测算法（YOLO 系列、视觉语言模型、OCR 等），实现实时检测、时间窗口告警验证、告警录像与消息推送的完整闭环。

**核心能力：**

- 多路视频流实时解码与分析（支持硬解：NVDEC / Jetson / RK MPP）
- 可视化拖拽式工作流编排（视频源 → 算法 → 条件 → 告警）
- 脚本化算法插件，支持热重载与版本回滚
- ROI 热区配置（预掩码 / 裁剪推理 / 后过滤三种模式）
- 时间窗口统计告警（防误报）+ 告警抑制
- 告警录像（警前/警后缓冲）与标注截图
- 多通道消息推送：MQTT / RabbitMQ / HTTP / Webhook（钉钉、Bark）
- 多用户权限隔离与许可证额度管理

**默认端口：**

| 服务 | 端口 | 说明 |
|---|---|---|
| Web 前端 | 8080 | 用户操作界面 |
| Web API | 5002 | 后端接口 |
| RabbitMQ 管理台 | 15672 | 仅 CUDA 版本内置 |
| MediaMTX | 8554 / 8889 / 8189 | WebRTC 实时预览 |

---

## 2. 快速上手

完成部署后（见[第 15 节](#15-部署方式)），按以下路径快速跑通第一个检测任务：

1. **登录系统**：浏览器访问 `http://<服务器IP>:8080`，使用管理员账号登录。
2. **添加视频源**：进入「视频源」页面，点击「新建」，填写 RTSP 地址或上传本地视频文件。
3. **上传模型**：进入「模型」页面，上传 `.pt` / `.onnx` / `.rknn` 等模型文件；可使用「一键快速配置」自动生成默认算法和告警工作流。
4. **创建算法**：进入「算法」页面，新建算法并关联模型（或使用脚本模板）。
5. **编排工作流**：进入「工作流」页面，新建编排，在编辑器中拖入「视频源 → 算法 → 告警输出」三个节点并连线，保存后点击「启用」。
6. **查看告警**：进入「告警」页面查看触发的告警记录、标注图和录像。

---

## 3. 登录与用户权限

### 3.1 登录

- 访问 `/login` 页面，输入用户名和密码登录。
- 登录成功后系统签发 JWT（有效期 24 小时），到期后需重新登录。

### 3.2 角色与权限

| 角色 | 权限范围 |
|---|---|
| `admin`（管理员） | 全部功能：用户管理、系统设置、许可证、API Key、模型上传等 |
| `user`（普通用户） | 仅可查看和操作**自己创建**的资源（视频源、算法、工作流、告警、导出任务等） |

### 3.3 用户管理（仅管理员）

入口：左侧菜单「用户管理」（`/users`）

- 查看用户列表、创建新用户
- 修改用户密码、角色（admin / user）
- 启用 / 禁用账号、删除用户（不能删除自己）

---

## 4. 仪表板

入口：左侧菜单「仪表板」（`/dashboard`）

仪表板提供系统运行总览：

- **统计卡片**：视频源、工作流、今日告警等关键指标数量
- **系统监控**：CPU、内存、磁盘等资源占用实时曲线
- **通道告警图表**：各视频源通道的告警分布统计
- **最近告警**：最新触发的告警记录速览，可点击跳转详情

---

## 5. 视频源管理

入口：左侧菜单「视频源」（`/video-sources`）

### 5.1 添加视频源

点击「新建」，填写以下信息：

- **名称 / 源编码（source_code）**：源编码是唯一标识，用于环形缓冲区命名和 API 调用
- **流地址**：支持 `rtsp://`、`http://`（HTTP-FLV/HLS）、本地文件路径
- **解码分辨率**：解码后的宽高（ROI 坐标基于此分辨率），新建/导入默认 `640×360`
- **目标帧率（FPS）**：分析用帧率
- **启用状态**：创建后可随时启停

也可以使用「流探测」功能（内部调用 ffprobe）自动读取流的分辨率和帧率。

### 5.2 其他添加方式

- **上传视频文件**：支持 mp4 / avi / mov / mkv / flv / m4v / webm / wmv，上传后作为文件源循环分析
- **ONVIF 扫描**：自动发现局域网内的 ONVIF 摄像头，选择 Profile 后批量导入
- **海康 NVR 批量导入**：填写 NVR 地址和凭据，自动发现其下所有通道并批量创建视频源

### 5.3 视频源操作

- **启停**：切换 enabled 状态控制该源是否解码
- **实时预览**：点击「预览」通过 WebRTC 查看实时画面（需 MediaMTX 服务运行）
- **最新检测帧**：查看该源最近一帧的检测标注图
- **健康状态**：查看解码健康指标（帧计数、距最近一帧的时间、连续错误数）及健康事件日志（无帧警告、进程退出、低帧率、硬解资源等待、软解兜底等 10 类事件）
- **编辑 / 删除**：修改参数或移除视频源

> **许可证限制**：免费试用版仅允许 1 路视频源，超额源会自动停跑。

---

## 6. 模型管理

入口：左侧菜单「模型」（`/models`）

### 6.1 上传模型

支持格式：`.pt`、`.onnx`、`.engine`、`.bin`、`.tflite`、`.xml`、`.param`、`.json`、`.rknn`

三种导入方式：

1. **本地上传**：直接选择模型文件上传
2. **URL 导入**：填写模型文件下载地址
3. **Hugging Face 导入**：填写 repo_id、文件名、revision；可配置 HF 镜像加速（`HF_USE_MIRROR`）和访问令牌（`HF_TOKEN`）

### 6.2 模型操作

- **筛选**：按模型类型、推理框架筛选列表
- **一键快速配置（quick-setup）**：基于模型自动生成默认算法 + 告警工作流模板，适合快速验证新模型
- **查看关联算法**：查看哪些算法在使用该模型
- **编辑 / 下载 / 删除**

---

## 7. 算法管理

入口：左侧菜单「算法」（`/algorithms`）

### 7.1 算法类型

| 类型 | 说明 |
|---|---|
| `script`（脚本算法） | 基于 `app/user_scripts/` 下的 Python 脚本，实现 `init()` 和 `process()` 接口 |
| `vl`（视觉语言模型） | 调用 VL 大模型进行图像理解（可用性视运行环境而定） |
| `ocr`（OCR 算法） | 基于 PaddleOCR 的文字识别 |
| `cascade`（组合检测） | 画布式级联检测：检测器 + 谓词 + 逻辑规则（AND/OR/NOT）组合 |

### 7.2 创建算法

可通过「算法配置向导」（`/algorithms/wizard`）向导式创建，或直接新建。主要配置项：

- **基本信息**：名称、描述、类型、脚本路径
- **关联模型**：选择一个或多个已上传的模型
- **标签配置**：`label_name`（标签名）、`label_color`（标注颜色）
- **执行配置**：
  - `interval_seconds`：执行间隔（抽帧）
  - `runtime_timeout`：单次执行超时
  - `memory_limit_mb`：内存上限
- **算法级窗口检测**：`enable_window_check`、`window_size`（秒）、`window_mode`（ratio / consecutive / count）、`window_threshold`

自适应 YOLO 检测支持两种推理模式：

- **Letterbox（默认）**：整帧按比例缩放并补边，速度和资源占用更稳定
- **SAHI**：把高分辨率画面切成重叠窗口分别推理，再将坐标映射回整帧并按类别融合重复框；可配置切片宽高、重叠率、IOS/IOU 融合阈值、整帧补充推理和最大切片数

> SAHI 不能恢复解码阶段已经丢失的细节。要提升小目标效果，应先把对应视频源的解码分辨率调高（例如 `1280×720` 或 `1920×1080`），再使用接近模型输入尺寸的切片；其推理次数和耗时会随切片数增加。

### 7.3 算法测试与预览

- **图片测试**：上传一张图片，立即运行算法并查看标注结果，用于验证配置正确性
- **级联预览**：对 cascade 类型算法预览组合检测效果

---

## 8. 脚本管理

入口：左侧菜单「脚本」（`/scripts`）

- **脚本列表 / 上传 / 在线编辑 / 删除**：管理 `app/user_scripts/` 下的算法脚本
- **语法校验**：保存前校验脚本语法与接口规范（必须包含 `SCRIPT_METADATA`、`init()`、`process()`）
- **脚本模板**：提供各节点类型的标准模板，可直接基于模板开发
- **配置 Schema 查询**：查看脚本声明的可配置项（用于 UI 自动生成配置表单）
- **版本管理与回滚**：算法脚本支持多版本保存，可随时回滚到历史版本

**脚本接口规范：**

```python
SCRIPT_METADATA = {
    "name": "my_algorithm",
    "version": "1.0",
    "description": "自定义检测算法",
    "author": "Your Name",
    "options": []  # UI 配置项声明
}

def init(config):
    """初始化：加载模型等，返回状态对象"""
    return state

def process(frame, roi_regions, state, upstream_results=None):
    """
    处理单帧（RGB 格式 numpy 数组）
    返回: {"detections": [{"box": [x1,y1,x2,y2], "label": "person", "confidence": 0.95}]}
    """
    pass
```

---

## 9. 工作流编排

### 9.1 工作流列表

入口：左侧菜单「工作流」（`/workflows`）

- **新建 / 编辑 / 删除**编排
- **启用 / 停用**单个编排（启用后由独立的 worker 进程执行）
- **批量操作**：批量启用、批量停用、批量删除
- **批量配置**：对多个编排统一修改配置，支持 dry_run 预检和版本号乐观锁（防止并发冲突）
- **模板复制**：将一个编排作为模板，批量复制到多个视频源（自动替换源节点）
- **抓帧**：从视频源抓取当前帧，用于 ROI 绘制底图
- **编排测试**：上传图片或视频，对整个工作流进行端到端测试；历史测试结果在「编排测试结果」页面（`/workflow-test-results`）查看

### 9.2 工作流编辑器

入口：工作流列表 → 点击编排进入编辑器（`/workflows/editor/:id`）

编辑器为拖拽式画布，分为三个区域：

- **左侧组件面板**：按分类列出可用节点（视频源、算法、外部 API、条件分支、函数计算、图像处理、输出）
- **中间画布**：拖入节点、连线（支持条件分支的 true/false 边）
- **右侧属性面板**：选中节点后配置其参数

**辅助工具：**

- **ROI 绘制器**：在抓帧底图上用鼠标绘制多边形热区
- **时间计划编辑器**：以周日历方式配置时间启用区间
- **测试面板**：编辑器内直接对当前编排发起测试

**典型编排示例（人员检测告警）：**

```
视频源节点 → 热区绘制节点 → 算法节点（人员检测）→ 检测条件节点 → 告警输出节点
```

**保存与启用：** 编排保存后处于停用状态，需在列表页点击「启用」才会启动 worker 进程开始分析。

### 9.3 在同型号盒子之间迁移模板

迁移前，必须在每台盒子的部署环境中设置产品型号代码。同一硬件型号使用相同值，不同型号必须使用不同值：

```env
DEVICE_MODEL_CODE=VB-RK3588-16G-V2
```

1. 在来源盒子的「编排模板」列表打开更多操作，选择「导出迁移包」。
2. 按目标环境选择是否携带模型文件；不携带时，目标盒子必须已有文件内容及运行元数据完全一致的模型（框架、输入尺寸、类别、后处理和版本等）。
3. 在目标盒子点击「导入模板」并选择 `.vbt.zip` 文件。系统会先读取清单并严格比较 `DEVICE_MODEL_CODE`，型号不一致时不会上传完整文件。
4. 根据预检结果映射缺失模型、处理重名资源，并重新填写 Webhook、外部 API 或视觉语言服务的密钥。
5. 校验通过后导入。新模板保持停用且不绑定视频源，可再通过「应用到视频源」生成运行编排。

迁移包包含模板结构、算法、脚本、Hook、外部 API 非敏感配置和模型校验信息；不会包含视频源、许可证、告警历史和任何已保存的密钥。模型、脚本和配置会通过 SHA-256 校验，导入失败时不会保留半成品资源。

---

## 10. 工作流节点详解

### 10.1 视频源节点（source）

| 参数 | 说明 |
|---|---|
| `data_id` | 关联的视频源 ID |

从该源的共享内存环形缓冲区读取视频帧，作为整条工作流的输入。一个编排通常只有一个源节点。

### 10.2 算法节点（algorithm）

| 参数 | 说明 |
|---|---|
| `data_id` | 关联的算法 ID |
| `interval_seconds` | 执行间隔（秒），实现抽帧分析 |
| `config.roi_regions` | 可选，覆盖算法默认 ROI 配置 |

**ROI 配置优先级**（高 → 低）：

1. 上游热区绘制节点传入的 ROI
2. 算法节点 `config.roi_regions`
3. 算法数据库默认配置

### 10.3 外部 API 节点（external_api）

调用「外部 API」页面配置的 HTTP 接口（如第三方识别服务）。

| 参数 | 说明 |
|---|---|
| `data_id` | 外部 API 条目 ID |
| `interval_seconds` | 调用间隔 |

### 10.4 检测条件节点（condition）

对上游检测结果做条件判断，输出 true/false 两个分支。三种判断方式：

**① 数量判断（count）**
- `target_count`：目标数量
- `comparison_type`：`>=` 或 `==`
- `labels`：类别过滤（如只统计 person）

**② 数量突变（count_change）** —— 检测窗口内数量突变（如人员突然聚集/散去）
- `window_size`：统计窗口（秒）
- `direction`：突变方向（both 双向 / up 上升 / down 下降）
- `relative_threshold` / `absolute_threshold`：相对/绝对变化阈值
- `confirmation_count`：连续确认次数（防抖动）

**③ OCR 文本匹配（ocr_text）** —— 配合 OCR 算法使用
- `pattern_type`：`keywords`（关键词）或 `regex`（正则）
- `keywords` + `keyword_logic`（any 任一命中 / all 全部命中）
- `regex_pattern`：正则表达式
- `case_sensitive`：大小写敏感

### 10.5 时间启用区间节点（time_schedule）

- `weekly_schedule`：按周日历配置多个时间段（如「工作日 08:00-18:00」）
- 只有在启用时间段内，下游节点才会执行，用于上下班时段差异化监控

### 10.6 热区绘制节点（roi_draw）

| 参数 | 说明 |
|---|---|
| `roi_regions[].name` | 区域名称 |
| `roi_regions[].polygon` | 多边形顶点（0-1 相对坐标，与实际分辨率无关） |
| `roi_regions[].mode` | `pre_mask` 预掩码（检测前遮挡，快）/ `crop_infer` 裁剪推理（只推理 ROI 区域，省算力）/ `post_filter` 后过滤（全图检测后筛选，准） |
| `roi_regions[].anchor` | 判定点（如 `bottom_center` 底部中心，适合判断人脚位置） |

### 10.7 函数计算节点（function）

对一至两个上游算法节点的检测结果做数学计算。

**单输入函数：** `height_ratio_frame`（框高/帧高）、`width_ratio_frame`、`area_ratio_frame`（面积占比）、`size_absolute`（绝对像素尺寸）

**双输入函数：** `area_ratio`（A面积/B面积）、`height_ratio`、`iou_check`（交并比）、`distance_check`（中心距离）

> 连接两个算法节点到函数节点时，先连的为输入 A，后连的为输入 B。

### 10.8 目标尺寸筛选节点（detection_filter）

对一个上游节点的检测框进行后过滤，只向下游传递符合尺寸条件的目标。多个规则可通过串联节点组合。

| 参数 | 说明 |
|---|---|
| `config.dimension` | `height` 高度 / `width` 宽度 |
| `config.unit` | `pixel` 绝对像素 / `ratio` 占原始画面比例（0-1） |
| `config.comparison` | `gte` 大于等于（最小值）/ `lte` 小于等于（最大值） |
| `config.threshold` | 非负阈值；比例模式必须在 0-1 之间 |

没有有效检测框的语义结果会被过滤。节点必须且只能连接一个上游检测结果节点。

### 10.9 告警输出节点（alert）

| 参数 | 说明 |
|---|---|
| `alert_level` | 告警级别：info / warning / error / critical |
| `alert_message` | 告警消息模板 |
| `alert_type` | 告警类型（用于筛选与统计） |
| `message_format` | 消息格式：detailed / simple / summary |
| `trigger_condition` | 时间窗口触发条件（见下） |
| `suppression` | 告警抑制（见下） |
| `vl_validation` | VL 核验：触发后调用视觉语言模型二次确认（需在系统设置配置 VL 服务） |
| `publish_to_mq` | 是否投递到全局消息通道（MQTT/RabbitMQ/HTTP） |

**时间窗口触发条件（防误报核心机制）：**

```json
{
  "enable": true,
  "window_size": 30,
  "mode": "ratio",
  "threshold": 0.3
}
```

- `ratio`（比例模式）：窗口 30 秒内命中帧占比 ≥ 30% 才告警
- `consecutive`（连续模式）：连续命中 N 次才告警
- `count`（计数模式）：窗口内累计命中 N 次才告警

**告警抑制：**

```json
{ "enable": true, "seconds": 60 }
```

同类告警触发后冷却 60 秒，避免刷屏。

### 10.10 Webhook 推送节点（webhook）

只能挂在告警节点之后，将告警推送到第三方：

| Provider | 说明 |
|---|---|
| `dingtalk` | 钉钉群机器人（支持加签 `signing_secret`） |
| `bark` | iOS Bark 推送（需 `device_key`） |
| `generic` | 通用 HTTP Webhook（自定义域名需加入 `WEBHOOK_ALLOWED_HOSTS` 白名单） |

模板参数：`title_template` / `body_template` / `payload_template` 支持 `{{alert.*}}` 占位符；`include_media_urls` 控制是否附带标注图/录像链接；支持超时与失败重试（`max_attempts` / `retry_backoff_seconds`）。

---

## 11. 告警管理

### 11.1 告警列表

入口：左侧菜单「告警」（`/alerts`）

- **检索**：按视频源、工作流、告警类型、时间范围组合筛选，分页展示
- **详情**：查看告警标注图、原始截图和录像视频
- **告警大屏**（`/alert-wall`）：无框架全屏展示页面，适合监控中心投屏（免登录布局）

### 11.2 告警录像机制

- 系统为每路视频源维护共享内存环形缓冲区（容量 = 帧率 × 缓冲时长）
- 告警触发时，自动摘取**警前 N 秒**（`PRE_ALERT_DURATION`）历史帧 + 继续录制**警后 N 秒**（`POST_ALERT_DURATION`）
- 输出帧率由 `RECORDING_FPS` 控制（建议 5-15）
- 录像在后台线程异步完成，不阻塞实时分析

### 11.3 媒体访问与安全

- 标注图和录像通过签名 URL 访问（`MEDIA_URL_SIGNING_ENABLED`，有效期 `MEDIA_URL_TTL_HOURS`）
- 消息推送时媒体交付支持三种模式（见 13.5）：URL 链接 / base64 内嵌 / 对象存储

### 11.4 告警导出

入口：左侧菜单「告警导出」（`/alerts/exports`）

- 按筛选条件创建导出任务，后台异步打包 ZIP（含图片、录像、清单文件）
- 支持查看任务进度、下载、取消、删除

### 11.5 数据保留与清理

系统周期性自动清理：

| 数据类型 | 默认保留期 / 上限 |
|---|---|
| 告警图片、录像 | 7 天 |
| 告警记录 | 30 天 |
| 窗口检测统计 | 24 小时 |
| 图片容量上限 | 10 GB |
| 视频容量上限 | 20 GB |
| 磁盘最低剩余 | 10 GB |

磁盘水位保护：使用率 ≥ 80% 停止录像，≥ 90% 只记录元数据（均可在系统设置调整）。

---

## 12. 外部 API 管理

入口：左侧菜单「外部 API」（`/external-apis`）

配置可被工作流「外部 API 节点」调用的第三方 HTTP 接口：

- **基本信息**：名称、endpoint_url、method（GET/POST 等）、headers、超时时间
- **请求模板**（request_template）：定义发送的数据格式
- **输入/输出 Schema**（input/output schema）：声明接口的数据结构
- **输出映射**（output_mapping）：将接口返回字段映射为工作流内部结果
- **启用开关**：停用后引用它的节点不再发起调用

---

## 13. 系统设置

入口：左侧菜单「系统设置」（`/system-settings`，仅管理员），共 10 个配置页签。

### 13.1 许可证

- 查看当前许可证状态、节点 ID（`node_id`）、额度
- 上传 `.license` 文件安装付费许可证（Ed25519 签名，绑定节点）
- **免费试用版限额：1 路视频源 + 3 个算法**；超额资源自动停跑，额度恢复后自动恢复
- 注意：系统时间回拨超过 5 分钟会降级为免费版

### 13.2 推理资源保护

- **共享推理**：多算法共享模型实例，减少显存/内存占用（队列长度、批大小、批等待毫秒、请求超时、模型空闲回收秒数）
- **多 GPU 动态调度（x86 CUDA）**：至少两张可见 NVIDIA GPU 时，共享 Ultralytics/PaddleOCR 模型按预计显存占用率选择物理 GPU；支持每卡保留显存、冷启动预留、允许卡列表、CUDA OOM 换卡一次和 NVML 失效策略。ONNX、RKNN 与直连 YOLO 暂不在 V1 调度范围内
- **内存准入**：加载新模型前检查剩余内存（系统预留 MB/百分比、新模型默认占用、安全边际百分比）
- **OOM 熔断**：模型连续失败达到阈值后熔断（失败阈值、熔断时长、稳定重置时长、重启退避上限）
- 修改后 worker 约 5 秒内热生效

### 13.3 视频解码

- **仅解码关键帧**：开启后大幅降低解码开销（适合低帧率分析场景）；保存后 worker 自动重启应用

### 13.4 录像与存储

- 录像总开关、警前/警后秒数、录像帧率
- 容量上限：视频 GB / 图片 GB / 磁盘最低剩余 GB
- 磁盘水位阈值：停录水位（默认 80%）、仅元数据水位（默认 90%）
- 页面展示当前磁盘与目录用量及压力等级；修改约 5 秒热生效

### 13.5 告警媒体

配置告警消息中图片/视频的交付方式，三选一：

| 模式 | 说明 |
|---|---|
| `url`（默认） | 消息中带盒子签名 URL，需配置 `public_base_url`（外网可访问地址） |
| `inline` | 消息内嵌 base64 图片（可调大小上限、最长边、JPEG 质量） |
| `object_storage` | 上传到 S3 兼容对象存储后返回预签名 URL（endpoint/region/bucket/AK/SK 等，附「测试对象存储」按钮） |

另可配置异步重试参数，并查看失败投递统计、一键重试失败投递。

### 13.6 消息投递

全局告警消息通道，**三选一**，均支持「测试连接」：

- **MQTT**（默认）：host/port/用户名/密码/topic_prefix（默认 `video/alert`），主题格式 `{prefix}/{node_id}/{alert_type}`，QoS 1
- **RabbitMQ**：host/port/vhost/exchange/routing_key/exchange_type（topic 或 direct）
- **HTTP API**：endpoint_url + HMAC-SHA256 签名（hmac_secret、防重放 nonce、300 秒时钟窗），页面可生成接收端实现 Prompt

消息采用 outbox 模式保证至少一次投递，含 `alert.created` 与 `alert.media.ready` 两阶段事件，`event_id` 幂等去重。详细格式见 `docs/message_queue_integration.md`。

### 13.7 钉钉通知（运维通知）

面向运维的钉钉机器人告警（与业务告警 Webhook 节点不同）：

- webhook_url、加签 secret
- 开关项：磁盘水位通知、清理失败通知、告警激增通知（时间窗 + 增长阈值）
- 冷却时间；附「发送测试通知」按钮

### 13.8 视频轮转

视频源数量超过解码能力时，分批轮巡分析：

- `batch_size`：每批同时分析的路数
- `dwell_seconds`：每批驻留时长（秒）
- 页面显示授权后候选数、有效并发、检测/启动/排队/排空状态，以及理论最短、实测 P95 和保护上界复访时间
- 每路视频源独立计算驻留时间；单路建链或模型加载缓慢不会暂停其他检测槽
- 轮转采用严格解码上限：告警后录仍在排空时，新源等待旧解码器释放，避免切批瞬间 CPU、CMA 或显存峰值翻倍
- 软解兜底默认最多 2 路；稳定扩容场景不要将 `HW_DECODE_SW_FALLBACK_MAX` 配置为 `0`

容量规划建议：优先使用摄像头低码率子码流做分析，主码流只用于预览或告警录像；`batch_size` 表示同时运行容量，不等于可承诺的总接入路数。最终规格应在目标设备上以实际编码、分辨率、FPS 和模型组合进行阶梯压测，并以复访时间 P95 作为轮转盲区指标。

### 13.9 API Key

创建/启停对外 OpenAPI 的访问密钥，配合 `/openapi/v1/*` 接口使用（见第 14 节）。

### 13.10 VL 核验

配置视觉语言模型服务（base_url、model、extra_body 等），供告警节点的 VL 二次核验功能使用。

---

## 14. 对外 OpenAPI 集成

面向第三方平台的程序化管理接口，使用 API Key 鉴权（在「系统设置 → API Key」创建）。

**主要能力（`/openapi/v1`）：**

- 创建 / 修改视频源、更新流地址
- 查询工作流模板列表
- 基于「模板 + 视频源」一键创建并激活编排（`POST /workflow-activations`）
- 查询工作流列表、停用工作流

完整接口定义见 `docs/openapi.yaml`，使用指南见 `docs/openapi_usage.md`；Web 界面「API 文档」页（`/api-docs`）也可在线查看。

---

## 15. 部署方式

### 15.1 生成 Docker Compose

仓库通过 `scripts/generate_compose.sh` 生成根目录的 `docker-compose.yml`，不再分别维护各平台和 `no-mqtt` 副本。

| 生成参数 | 适用平台 | 默认解码器 | 特点 |
|---|---|---|---|
| `--platform cpu` | 通用 CPU | ffmpeg 软解 | 通用 x86 CPU 服务 |
| `--platform cuda` | x86 + NVIDIA GPU | NVDEC 硬解（按 NVML 利用率自动限并发） | NVIDIA runtime |
| `--platform jetson` | Jetson Orin（JetPack 6.2.1） | nvv4l2decoder 硬解 | runtime: nvidia，额外 storage-guard 服务 |
| `--platform rknn` | RK3588 等瑞芯微平台 | rk_mpp 硬解 | WEB_CONCURRENCY=1 |

MQTT 默认关闭；可通过 `--with-mqtt`、`--with-rabbitmq`、`--with-mediamtx` 独立加入可选服务。

**启动示例：**

```bash
# CPU 版本
./scripts/generate_compose.sh --non-interactive --platform cpu --force
docker compose up -d

# CUDA 版本
./scripts/generate_compose.sh --non-interactive --platform cuda --force
docker compose up -d

# 查看日志
docker logs video-ba-pipe-cpu -f
```

首次启动时 `db-init` 服务会自动完成数据库初始化/迁移。`db-init`、`api` 和 `jobs` 使用不含推理框架的 control 镜像；只有 `worker` 使用对应平台的重型推理镜像。两类镜像由同一平台工作流按 commit 成对发布，生产环境建议在 `.env` 设置 `VIDEO_BA_PIPE_RELEASE=<完整 commit SHA>`。

### 15.2 本地开发

```bash
pip install -r requirements.txt
python3 -m app.setup_database   # 初始化数据库
python3 -m app.jobs             # 启动后台任务（独立终端）
python app/main.py              # 启动 worker（编排执行）
python app/web/webapp.py        # 启动 Web API（另开终端）
```

### 15.3 关键环境变量（.env，参考 env.example）

| 分类 | 变量 |
|---|---|
| 基础 | `COMPANY_NAME`（页头品牌）、`JWT_SECRET`、`PUBLIC_BASE_URL` |
| 数据库 | `DB_BACKEND` / `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD`（或 SQLite `DB_PATH`） |
| 存储路径 | `FRAME_SAVE_PATH`、`VIDEO_SAVE_PATH`、`EXPORT_SAVE_PATH`、`MODEL_SAVE_PATH`、`USER_SCRIPTS_ROOT` 等 |
| 解码 | `VIDEO_DECODER_TYPE`、`IS_EXTREME_DECODE_MODE`、`FFMPEG_SW_DECODER_THREADS`、硬解预算 `HW_DECODE_*` |
| 分析/录像 | `ANALYSIS_TARGET_FPS`、`RECORDING_FPS`、`PRE_ALERT_DURATION`、`POST_ALERT_DURATION` |
| 告警 | `ALERT_SUPPRESSION_DURATION`（全局抑制）、`ALERT_*_RETENTION_DAYS`（保留期） |
| 其他 | `HF_USE_MIRROR`/`HF_TOKEN`（模型下载）、`MEDIAMTX_*`（预览）、`WEBHOOK_ALLOWED_HOSTS`、`LICENSE_PUBLIC_KEY_PATH` |

> **注意**：MQTT / RabbitMQ / HTTP 消息通道的连接参数**不再使用环境变量**，统一在「系统设置 → 消息投递」页面配置。

---

## 16. 常见问题排查

| 现象 | 排查方向 |
|---|---|
| 工作流启用后无反应 | 检查工作流 JSON 节点连线是否完整；查看 worker 日志 `docker logs video-ba-pipe-cpu \| grep WorkflowWorker` |
| 有视频但无检测结果 | 检查 ROI 配置是否过严、模型路径是否正确、置信度阈值是否过高；用算法「图片测试」验证 |
| 算法脚本加载失败 | 检查脚本是否含 `SCRIPT_METADATA`/`init`/`process`；用脚本页「语法校验」；确认脚本路径正确 |
| 内存占用过高 | 减小 `RINGBUFFER_DURATION` / `RECORDING_FPS`；开启共享推理；检查算法 `memory_limit_mb` |
| 录像文件缺失/过短 | 确认 `RINGBUFFER_DURATION ≥ PRE_ALERT_DURATION`；检查磁盘水位是否触发停录（≥80%） |
| 视频源频繁重启 | 查看视频源「健康日志」；网络不稳定时检查 `NO_FRAME_CRITICAL_THRESHOLD`；Jetson/RK 平台注意硬解 CMA 资源上限（超限会自动软解兜底） |
| 消息队列无推送 | 「系统设置 → 消息投递」点「测试连接」；确认告警节点开启了 `publish_to_mq`；查看失败投递统计并重试 |
| Webhook 推送失败 | 自定义域名需加入 `WEBHOOK_ALLOWED_HOSTS`；钉钉需确认加签 secret 正确 |
| 实时预览无法播放 | 确认 mediamtx 容器运行中；先在「系统设置」检查预览配置；防火墙放行 8554/8889/8189 |
| 许可证额度不足 | 免费版限 1 源 + 3 算法；在「系统设置 → 许可证」安装付费许可证或删减资源 |

**日志查看：**

```bash
docker logs video-ba-pipe-cpu -f                              # 全量日志
docker logs video-ba-pipe-cpu 2>&1 | grep -E "(WorkflowWorker|Orchestrator)"  # 按组件过滤
```
