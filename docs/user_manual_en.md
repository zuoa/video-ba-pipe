# Video BA Pipe — User Operation Manual

> Version: 2026-08 | Applies to: current main branch
> This manual is written for system operators and administrators. It describes every major feature of the Video BA Pipe video analytics system and how to use it.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Quick Start](#2-quick-start)
3. [Login & User Permissions](#3-login--user-permissions)
4. [Dashboard](#4-dashboard)
5. [Video Source Management](#5-video-source-management)
6. [Model Management](#6-model-management)
7. [Algorithm Management](#7-algorithm-management)
8. [Script Management](#8-script-management)
9. [Workflow Orchestration](#9-workflow-orchestration)
10. [Workflow Node Reference](#10-workflow-node-reference)
11. [Alert Management](#11-alert-management)
12. [External API Management](#12-external-api-management)
13. [System Settings](#13-system-settings)
14. [OpenAPI Integration](#14-openapi-integration)
15. [Deployment](#15-deployment)
16. [Troubleshooting](#16-troubleshooting)

---

## 1. System Overview

Video BA Pipe is a video stream analytics system. It ingests RTSP / HTTP-FLV / HLS streams and local video files, runs AI detection algorithms (YOLO family, vision-language models, OCR) through a **visual node-based workflow editor**, and delivers a complete alert pipeline: real-time detection → time-window verification → alert recording → message delivery.

**Core capabilities:**

- Real-time multi-channel video decoding and analysis (hardware decode: NVDEC / Jetson / RK MPP)
- Visual drag-and-drop workflow editor (Source → Algorithm → Condition → Alert)
- Script-based algorithm plugins with hot reload and version rollback
- ROI hot-zone configuration (three modes: pre-mask / crop-infer / post-filter)
- Time-window statistical alerting (false-alarm suppression) + alert cooldown
- Alert recording with pre/post-event buffering and annotated snapshots
- Multi-channel message delivery: MQTT / RabbitMQ / HTTP / Webhook (DingTalk, Bark)
- Multi-user permission isolation and license quota management

**Default ports:**

| Service | Port | Description |
|---|---|---|
| Web frontend | 8080 | User interface |
| Web API | 5002 | Backend API |
| RabbitMQ console | 15672 | Bundled with the CUDA build only |
| MediaMTX | 8554 / 8889 / 8189 | WebRTC live preview |

---

## 2. Quick Start

After deployment (see [Section 15](#15-deployment)), follow these steps to run your first detection task:

1. **Log in**: open `http://<server-ip>:8080` in a browser and sign in with the admin account.
2. **Add a video source**: go to "Video Sources", click "Create", and enter an RTSP URL or upload a local video file.
3. **Upload a model**: go to "Models" and upload a `.pt` / `.onnx` / `.rknn` model file. You can use "Quick Setup" to auto-generate a default algorithm and alert workflow.
4. **Create an algorithm**: go to "Algorithms" and create an algorithm bound to the model (or start from a script template).
5. **Build a workflow**: go to "Workflows", create a new workflow, drag three nodes — Video Source → Algorithm → Alert Output — onto the canvas, connect them, save, and click "Activate".
6. **View alerts**: go to "Alerts" to browse triggered alerts, annotated snapshots, and recorded clips.

---

## 3. Login & User Permissions

### 3.1 Login

- Open the `/login` page and sign in with a username and password.
- On success the system issues a JWT (valid for 24 hours); log in again after expiry.

### 3.2 Roles

| Role | Scope |
|---|---|
| `admin` | Full access: user management, system settings, license, API keys, model upload, etc. |
| `user` | Can only see and operate resources **created by themselves** (video sources, algorithms, workflows, alerts, export tasks, etc.) |

### 3.3 User Management (admin only)

Entry: left menu "Users" (`/users`)

- List users, create new users
- Change passwords and roles (admin / user)
- Enable / disable accounts, delete users (you cannot delete yourself)

---

## 4. Dashboard

Entry: left menu "Dashboard" (`/dashboard`)

The dashboard gives a system-wide overview:

- **Stat cards**: counts of video sources, workflows, today's alerts, and other key metrics
- **System monitor**: real-time CPU, memory, and disk usage
- **Channel alert chart**: alert distribution across video source channels
- **Recent alerts**: the latest triggered alerts with quick links to details

---

## 5. Video Source Management

Entry: left menu "Video Sources" (`/video-sources`)

### 5.1 Adding a Video Source

Click "Create" and fill in:

- **Name / Source code**: the source code is a unique identifier used for ring-buffer naming and API calls
- **Stream URL**: supports `rtsp://`, `http://` (HTTP-FLV/HLS), and local file paths
- **Decode resolution**: decoded width/height (ROI coordinates are based on this resolution)
- **Target FPS**: frame rate used for analysis
- **Enabled**: can be toggled at any time after creation

You can also use "Stream Probe" (ffprobe internally) to auto-detect the stream's resolution and frame rate.

### 5.2 Other Ways to Add Sources

- **Upload a video file**: mp4 / avi / mov / mkv / flv / m4v / webm / wmv; uploaded files are analyzed in a loop
- **ONVIF scan**: auto-discover ONVIF cameras on the LAN, pick a profile, and import in bulk
- **Hikvision NVR batch import**: enter the NVR address and credentials to discover all channels and create sources in bulk

### 5.3 Source Operations

- **Enable / disable**: controls whether the source is decoded
- **Live preview**: watch the live stream over WebRTC (requires the MediaMTX service)
- **Latest detection frame**: view the most recent annotated frame for this source
- **Health status**: decode health metrics (frame count, time since last frame, consecutive errors) and health event logs (10 event types: no-frame warning, process exit, low frame rate, HW-decode resource wait, SW-decode fallback, etc.)
- **Edit / delete**

> **License limit**: the free trial allows only 1 video source; excess sources are stopped automatically.

---

## 6. Model Management

Entry: left menu "Models" (`/models`)

### 6.1 Uploading Models

Supported formats: `.pt`, `.onnx`, `.engine`, `.bin`, `.tflite`, `.xml`, `.param`, `.json`, `.rknn`

Three import methods:

1. **Local upload**: select a model file directly
2. **URL import**: provide a download URL for the model file
3. **Hugging Face import**: provide repo_id, filename, and revision; mirror acceleration (`HF_USE_MIRROR`) and access token (`HF_TOKEN`) are supported

### 6.2 Model Operations

- **Filter** the list by model type and inference framework
- **Quick Setup**: auto-generate a default algorithm + alert workflow from the model — the fastest way to validate a new model
- **Linked algorithms**: see which algorithms use this model
- **Edit / download / delete**

---

## 7. Algorithm Management

Entry: left menu "Algorithms" (`/algorithms`)

### 7.1 Algorithm Types

| Type | Description |
|---|---|
| `script` | Python scripts under `app/user_scripts/` implementing the `init()` / `process()` interface |
| `vl` | Vision-language model inference for image understanding (availability depends on runtime environment) |
| `ocr` | Text recognition based on PaddleOCR |
| `cascade` | Canvas-based composite detection: detectors + predicates + logic rules (AND/OR/NOT) |

### 7.2 Creating an Algorithm

Use the wizard (`/algorithms/wizard`) or create directly. Main settings:

- **Basics**: name, description, type, script path
- **Models**: bind one or more uploaded models
- **Labels**: `label_name`, `label_color` (annotation color)
- **Execution**:
  - `interval_seconds`: execution interval (frame skipping)
  - `runtime_timeout`: per-run timeout
  - `memory_limit_mb`: memory cap
- **Algorithm-level window check**: `enable_window_check`, `window_size` (seconds), `window_mode` (ratio / consecutive / count), `window_threshold`

### 7.3 Testing & Preview

- **Image test**: upload an image to run the algorithm immediately and inspect the annotated result — the quickest way to validate a configuration
- **Cascade preview**: preview composite detection output for cascade-type algorithms

---

## 8. Script Management

Entry: left menu "Scripts" (`/scripts`)

- **List / upload / edit online / delete** algorithm scripts under `app/user_scripts/`
- **Syntax validation**: checks script syntax and interface compliance before saving (must define `SCRIPT_METADATA`, `init()`, `process()`)
- **Script templates**: standard templates for each node type to start from
- **Config schema query**: view the configurable options a script declares (used by the UI to auto-generate config forms)
- **Versioning & rollback**: algorithm scripts support multi-version history with one-click rollback

**Script interface contract:**

```python
SCRIPT_METADATA = {
    "name": "my_algorithm",
    "version": "1.0",
    "description": "Custom detection algorithm",
    "author": "Your Name",
    "options": []  # UI config option declarations
}

def init(config):
    """Initialize: load models, etc. Returns a state object."""
    return state

def process(frame, roi_regions, state, upstream_results=None):
    """
    Process a single frame (RGB numpy array).
    Returns: {"detections": [{"box": [x1,y1,x2,y2], "label": "person", "confidence": 0.95}]}
    """
    pass
```

---

## 9. Workflow Orchestration

### 9.1 Workflow List

Entry: left menu "Workflows" (`/workflows`)

- **Create / edit / delete** workflows
- **Activate / deactivate** individual workflows (activation spawns a dedicated worker process)
- **Batch operations**: batch activate, batch deactivate, batch delete
- **Batch config**: apply config changes to multiple workflows at once, with dry-run precheck and optimistic locking (version numbers) to prevent concurrent conflicts
- **Template copy**: use one workflow as a template and copy it to multiple video sources in bulk (source nodes are replaced automatically)
- **Frame capture**: grab the current frame from a video source — used as the background image for ROI drawing
- **Workflow test**: upload an image or video to run the whole workflow end-to-end; historical results are on the "Workflow Test Results" page (`/workflow-test-results`)

### 9.2 Workflow Editor

Entry: Workflow list → click a workflow (`/workflows/editor/:id`)

The editor is a drag-and-drop canvas with three areas:

- **Left component panel**: available nodes grouped by category (video source, algorithm, external API, condition branch, function, image processing, output)
- **Center canvas**: drop nodes and wire them together (supports true/false edges from condition branches)
- **Right property panel**: configure the selected node

**Helper tools:**

- **ROI drawer**: draw polygon hot-zones with the mouse on a captured frame
- **Time schedule editor**: configure active time windows on a weekly calendar
- **Test panel**: run a test against the current workflow without leaving the editor

**Typical workflow (person detection alert):**

```
Source node → ROI draw node → Algorithm node (person detection) → Condition node → Alert output node
```

**Save & activate:** a saved workflow stays inactive until you click "Activate" in the list — only then does a worker process start analyzing.

---

## 10. Workflow Node Reference

### 10.1 Source Node (source)

| Parameter | Description |
|---|---|
| `data_id` | ID of the bound video source |

Reads frames from the source's shared-memory ring buffer; it is the input of the whole workflow. A workflow usually has exactly one source node.

### 10.2 Algorithm Node (algorithm)

| Parameter | Description |
|---|---|
| `data_id` | ID of the bound algorithm |
| `interval_seconds` | Execution interval in seconds (frame skipping) |
| `config.roi_regions` | Optional ROI override |

**ROI priority** (high → low):

1. ROI passed in from an upstream ROI draw node
2. The algorithm node's `config.roi_regions`
3. The algorithm's default ROI in the database

### 10.3 External API Node (external_api)

Calls an HTTP endpoint configured on the "External APIs" page (e.g., a third-party recognition service).

| Parameter | Description |
|---|---|
| `data_id` | External API entry ID |
| `interval_seconds` | Call interval |

### 10.4 Condition Node (condition)

Evaluates upstream detection results and emits true/false branches. Three condition kinds:

**① Count (count)**
- `target_count`: target number
- `comparison_type`: `>=` or `==`
- `labels`: class filter (e.g., count only persons)

**② Count change (count_change)** — detects sudden changes within a window (e.g., crowd gathering/dispersing)
- `window_size`: statistics window (seconds)
- `direction`: change direction (both / up / down)
- `relative_threshold` / `absolute_threshold`: relative / absolute change thresholds
- `confirmation_count`: consecutive confirmations required (debounce)

**③ OCR text match (ocr_text)** — used with OCR algorithms
- `pattern_type`: `keywords` or `regex`
- `keywords` + `keyword_logic` (any / all)
- `regex_pattern`: regular expression
- `case_sensitive`

### 10.5 Time Schedule Node (time_schedule)

- `weekly_schedule`: multiple time windows on a weekly calendar (e.g., "weekdays 08:00–18:00")
- Downstream nodes only execute during active windows — useful for business-hours vs. after-hours policies

### 10.6 ROI Draw Node (roi_draw)

| Parameter | Description |
|---|---|
| `roi_regions[].name` | Zone name |
| `roi_regions[].polygon` | Polygon vertices (0–1 relative coordinates, resolution-independent) |
| `roi_regions[].mode` | `pre_mask` (mask before detection — fast) / `crop_infer` (crop and infer only the ROI — saves compute) / `post_filter` (detect full frame, then filter — accurate) |
| `roi_regions[].anchor` | Decision anchor point (e.g., `bottom_center`, good for judging where a person's feet are) |

### 10.7 Function Node (function)

Performs math on detection results from one or two upstream algorithm nodes.

**Single-input functions:** `height_ratio_frame` (box height / frame height), `width_ratio_frame`, `area_ratio_frame` (area share), `size_absolute` (absolute pixel size)

**Two-input functions:** `area_ratio` (area A / area B), `height_ratio`, `iou_check` (intersection over union), `distance_check` (center distance)

> When wiring two algorithm nodes into a function node, the first connection is input A and the second is input B.

### 10.8 Detection Size Filter Node (detection_filter)

Post-filters bounding boxes from one upstream result node and passes only matching detections downstream. Chain filter nodes to combine multiple rules.

| Parameter | Description |
|---|---|
| `config.dimension` | `height` or `width` |
| `config.unit` | `pixel` for absolute pixels or `ratio` for a 0-1 share of the original frame |
| `config.comparison` | `gte` for an inclusive minimum or `lte` for an inclusive maximum |
| `config.threshold` | Non-negative threshold; ratio values must be between 0 and 1 |

Semantic results without a valid bounding box are removed. The node must have exactly one upstream detection-result node.

### 10.9 Alert Output Node (alert)

| Parameter | Description |
|---|---|
| `alert_level` | Severity: info / warning / error / critical |
| `alert_message` | Alert message template |
| `alert_type` | Alert type (used for filtering and statistics) |
| `message_format` | detailed / simple / summary |
| `trigger_condition` | Time-window trigger (below) |
| `suppression` | Alert cooldown (below) |
| `vl_validation` | VL verification: re-confirm with a vision-language model on trigger (requires VL service in System Settings) |
| `publish_to_mq` | Whether to deliver via the global message channel (MQTT/RabbitMQ/HTTP) |

**Time-window trigger (the core false-alarm filter):**

```json
{
  "enable": true,
  "window_size": 30,
  "mode": "ratio",
  "threshold": 0.3
}
```

- `ratio`: alert only if ≥ 30% of frames in the 30-second window are positive
- `consecutive`: alert only after N consecutive positives
- `count`: alert only after N total positives within the window

**Suppression:**

```json
{ "enable": true, "seconds": 60 }
```

After an alert fires, the same type cools down for 60 seconds to avoid alert flooding.

### 10.10 Webhook Node (webhook)

Can only be attached after an alert node; pushes alerts to third parties:

| Provider | Description |
|---|---|
| `dingtalk` | DingTalk group bot (supports signing via `signing_secret`) |
| `bark` | iOS Bark push (requires `device_key`) |
| `generic` | Generic HTTP webhook (custom domains must be whitelisted in `WEBHOOK_ALLOWED_HOSTS`) |

Templates: `title_template` / `body_template` / `payload_template` support `{{alert.*}}` placeholders; `include_media_urls` controls whether snapshot/clip links are attached; timeout and retry are configurable (`max_attempts` / `retry_backoff_seconds`).

---

## 11. Alert Management

### 11.1 Alert List

Entry: left menu "Alerts" (`/alerts`)

- **Search**: combined filters by video source, workflow, alert type, and time range, with pagination
- **Details**: view the annotated snapshot, original frame, and recorded clip
- **Alert wall** (`/alert-wall`): a chromeless full-screen display page for monitoring-center projection (login-free layout)

### 11.2 Recording Mechanism

- Each video source maintains a shared-memory ring buffer (capacity = FPS × buffer duration)
- When an alert fires, the recorder extracts **N seconds before** the event (`PRE_ALERT_DURATION`) from buffer history and keeps recording **N seconds after** (`POST_ALERT_DURATION`)
- Output frame rate is controlled by `RECORDING_FPS` (recommended 5–15)
- Recording runs asynchronously in a background thread and never blocks real-time analysis

### 11.3 Media Access & Security

- Snapshots and clips are served via signed URLs (`MEDIA_URL_SIGNING_ENABLED`, TTL `MEDIA_URL_TTL_HOURS`)
- Three media delivery modes for message push (see 13.5): URL link / base64 inline / object storage

### 11.4 Alert Export

Entry: left menu "Alert Exports" (`/alerts/exports`)

- Create export tasks from filter criteria; the system asynchronously packs a ZIP (images + clips + manifest) in the background
- Track progress, download, cancel, or delete tasks

### 11.5 Retention & Cleanup

The system cleans up periodically:

| Data type | Default retention / cap |
|---|---|
| Alert images and clips | 7 days |
| Alert records | 30 days |
| Window-check statistics | 24 hours |
| Image storage cap | 10 GB |
| Video storage cap | 20 GB |
| Minimum free disk | 10 GB |

Disk water-mark protection: recording stops at ≥ 80% usage; metadata-only mode at ≥ 90% (both adjustable in System Settings).

---

## 12. External API Management

Entry: left menu "External APIs" (`/external-apis`)

Configure third-party HTTP endpoints callable from workflow "External API" nodes:

- **Basics**: name, endpoint_url, method (GET/POST, etc.), headers, timeout
- **Request template**: defines the outgoing payload format
- **Input/output schemas**: declare the API's data structures
- **Output mapping**: map response fields into workflow results
- **Enable toggle**: disabled entries stop receiving calls from nodes that reference them

---

## 13. System Settings

Entry: left menu "System Settings" (`/system-settings`, admin only) — 10 tabs.

### 13.1 License

- View current license status, node ID, and quota
- Install a paid license by uploading a `.license` file (Ed25519-signed, node-bound)
- **Free trial limits: 1 video source + 3 algorithms**; excess resources stop automatically and resume when quota is restored
- Note: rolling the system clock back more than 5 minutes downgrades the system to the free tier

### 13.2 Inference Resource Protection

- **Shared inference**: multiple algorithms share model instances to save VRAM/RAM (queue size, batch max size, batch wait ms, request timeout, model idle recycle seconds)
- **Dynamic multi-GPU placement (x86 CUDA)**: with at least two visible NVIDIA GPUs, shared Ultralytics/PaddleOCR workers are assigned by projected VRAM ratio. V1 supports per-GPU reserve, cold-start reservations, an allowlist, one cross-GPU retry after CUDA OOM, and an NVML failure policy. ONNX, RKNN, and direct YOLO loads are not managed in V1
- **Memory admission**: check remaining memory before loading new models (system reserve MB/percent, default new-model footprint, safety margin percent)
- **OOM circuit breaker**: trip a failing model after N consecutive failures (failure threshold, open duration, stable-reset duration, restart backoff cap)
- Changes hot-apply to workers within ~5 seconds

### 13.3 Video Decoding

- **Keyframes-only decoding**: drastically reduces decode cost (good for low-FPS analysis); workers restart automatically after saving

### 13.4 Recording & Storage

- Master recording switch, pre/post-alert seconds, recording FPS
- Caps: video GB / image GB / minimum free disk GB
- Disk water marks: stop-recording (default 80%), metadata-only (default 90%)
- The page shows current disk and directory usage with pressure levels; changes hot-apply in ~5 seconds

### 13.5 Alert Media

Choose one of three delivery modes for images/clips in alert messages:

| Mode | Description |
|---|---|
| `url` (default) | Message carries a signed URL served by the box; requires `public_base_url` (an externally reachable address) |
| `inline` | Embed base64 images in the message (tunable max bytes, max edge, JPEG quality) |
| `object_storage` | Upload to S3-compatible storage and return a presigned URL (endpoint/region/bucket/AK/SK, etc., with a "Test Object Storage" button) |

Async retry parameters are configurable, with failed-delivery statistics and one-click retry.

### 13.6 Message Delivery

The global alert channel — **pick one of three**, each with a "Test Connection" button:

- **MQTT** (default): host/port/username/password/topic_prefix (default `video/alert`); topic format `{prefix}/{node_id}/{alert_type}`, QoS 1
- **RabbitMQ**: host/port/vhost/exchange/routing_key/exchange_type (topic or direct)
- **HTTP API**: endpoint_url + HMAC-SHA256 signature (hmac_secret, anti-replay nonce, 300-second clock window); the page can generate an implementation prompt for your receiver

Messages use an outbox pattern for at-least-once delivery, with two-stage events `alert.created` and `alert.media.ready`, and idempotent dedup via `event_id`. Full format details: `docs/message_queue_integration.md`.

### 13.7 DingTalk Notifications (Ops)

DingTalk bot alerts for **operations** events (distinct from per-workflow Webhook nodes):

- webhook_url, signing secret
- Toggles: disk water-mark notification, cleanup-failure notification, alert-surge notification (window + growth threshold)
- Cooldown minutes; includes a "Send Test Notification" button

### 13.8 Source Rotation

When you have more sources than decode capacity, analyze them in rotating batches:

- `batch_size`: channels analyzed simultaneously per batch
- `dwell_seconds`: how long each batch stays active
- The page shows the number of eligible sources and the estimated full rotation cycle

### 13.9 API Keys

Create / enable / disable API keys for the external OpenAPI, used with `/openapi/v1/*` endpoints (see Section 14).

### 13.10 VL Verification

Configure the vision-language model service (base_url, model, extra_body, etc.) used by the alert node's VL re-verification feature.

---

## 14. OpenAPI Integration

A programmatic management API for third-party platforms, authenticated with API keys (created under "System Settings → API Keys").

**Main capabilities (`/openapi/v1`):**

- Create / update video sources, update stream URLs
- List workflow templates
- Create and activate a workflow from "template + video source" in one call (`POST /workflow-activations`)
- List workflows, deactivate workflows

Full spec: `docs/openapi.yaml`; usage guide: `docs/openapi_usage.md`; also browsable in the UI on the "API Docs" page (`/api-docs`).

---

## 15. Deployment

### 15.1 Docker Compose Variants

| Compose file | Platform | Default decoder | Notes |
|---|---|---|---|
| `docker-compose.yml` | Generic CPU | ffmpeg software decode | postgres + db-init + api + worker + frontend + mosquitto + mediamtx |
| `docker-compose.yml.x86+cuda` | x86 + NVIDIA GPU | NVDEC hardware decode (auto concurrency limit via NVML utilization) | Bundles RabbitMQ (console 15672, admin/admin123) |
| `docker-compose.yml.jetson` | Jetson Orin (JetPack 6.2.1) | nvv4l2decoder hardware decode | runtime: nvidia, extra storage-guard service |
| `docker-compose.yml.rknn` | RK3588 and other Rockchip platforms | rk_mpp hardware decode | WEB_CONCURRENCY=1 |
| `docker-compose.no-mqtt.yml[.*]` | All of the above | Same as above | Drops the bundled MQTT broker — for external MQTT or the RabbitMQ/HTTP channel |

**Startup examples:**

```bash
# CPU build
docker compose up -d

# CUDA build
docker compose -f docker-compose.yml.x86+cuda up -d

# Logs
docker logs video-ba-pipe-cpu -f
```

On first start, the `db-init` service runs database initialization/migration automatically.

### 15.2 Local Development

```bash
pip install -r requirements.txt
python3 -m app.setup_database   # initialize the database
python app/main.py              # start the worker (workflow execution)
python app/web/webapp.py        # start the Web API (separate terminal)
```

### 15.3 Key Environment Variables (.env — see env.example)

| Category | Variables |
|---|---|
| Basics | `COMPANY_NAME` (header branding), `JWT_SECRET`, `PUBLIC_BASE_URL` |
| Database | `DB_BACKEND` / `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` (or SQLite `DB_PATH`) |
| Storage paths | `FRAME_SAVE_PATH`, `VIDEO_SAVE_PATH`, `EXPORT_SAVE_PATH`, `MODEL_SAVE_PATH`, `USER_SCRIPTS_ROOT`, etc. |
| Decoding | `VIDEO_DECODER_TYPE`, `IS_EXTREME_DECODE_MODE`, `FFMPEG_SW_DECODER_THREADS`, HW-decode budget `HW_DECODE_*` |
| Analysis / recording | `ANALYSIS_TARGET_FPS`, `RECORDING_FPS`, `PRE_ALERT_DURATION`, `POST_ALERT_DURATION` |
| Alerts | `ALERT_SUPPRESSION_DURATION` (global cooldown), `ALERT_*_RETENTION_DAYS` |
| Misc | `HF_USE_MIRROR` / `HF_TOKEN` (model download), `MEDIAMTX_*` (preview), `WEBHOOK_ALLOWED_HOSTS`, `LICENSE_PUBLIC_KEY_PATH` |

> **Note**: MQTT / RabbitMQ / HTTP channel connection parameters are **no longer set via environment variables** — configure them on the "System Settings → Message Delivery" page.

---

## 16. Troubleshooting

| Symptom | What to check |
|---|---|
| Workflow does nothing after activation | Verify node wiring in the workflow JSON; check worker logs: `docker logs video-ba-pipe-cpu \| grep WorkflowWorker` |
| Video plays but no detections | ROI too restrictive? Model path correct? Confidence threshold too high? Validate with the algorithm "Image Test" |
| Algorithm script fails to load | Script must define `SCRIPT_METADATA` / `init` / `process`; run "Syntax Validation" on the Scripts page; verify the script path |
| High memory usage | Reduce `RINGBUFFER_DURATION` / `RECORDING_FPS`; enable shared inference; check algorithm `memory_limit_mb` |
| Recordings missing or too short | Ensure `RINGBUFFER_DURATION ≥ PRE_ALERT_DURATION`; check whether the disk water mark (≥80%) stopped recording |
| Video source restarts frequently | Check the source's health logs; for unstable networks review `NO_FRAME_CRITICAL_THRESHOLD`; on Jetson/RK, watch HW-decode CMA limits (auto SW fallback applies) |
| No message-queue push | Use "Test Connection" under System Settings → Message Delivery; confirm `publish_to_mq` is on for the alert node; check failed-delivery stats and retry |
| Webhook push fails | Custom domains must be whitelisted in `WEBHOOK_ALLOWED_HOSTS`; for DingTalk verify the signing secret |
| Live preview won't play | Confirm the mediamtx container is running; check preview config in System Settings; open firewall ports 8554/8889/8189 |
| License quota exceeded | Free tier allows 1 source + 3 algorithms; install a paid license under System Settings → License, or remove resources |

**Viewing logs:**

```bash
docker logs video-ba-pipe-cpu -f                                             # full log
docker logs video-ba-pipe-cpu 2>&1 | grep -E "(WorkflowWorker|Orchestrator)" # filter by component
```
