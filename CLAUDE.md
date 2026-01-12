# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Video BA Pipe is a video stream analysis system that processes RTSP/video streams, applies AI detection algorithms (YOLO-based), and generates alerts with video recording capabilities. The system uses a **node-based workflow architecture** with multi-process execution and shared memory buffers for efficient video frame processing.

**Core Features:**
- Real-time video stream processing (RTSP, HTTP-FLV, HLS, local files)
- Node-based workflow system for flexible pipeline configuration
- Script-based algorithm plugins with hot-reload support
- Time-window based alert detection (prevents false alarms)
- ROI (Region of Interest) hot-zone configuration
- Video recording with pre/post alert buffering
- RabbitMQ integration for alert publishing
- Web UI for configuration and monitoring

## Architecture

The system uses a **node-based workflow architecture with multi-process execution**:

### Main Components

1. **Orchestrator** (`app/core/orchestrator.py`)
   - Main entry point that manages workflow lifecycle
   - Spawns and monitors workflow worker processes
   - Creates shared memory ring buffers for each video source
   - Performs health checks and automatic recovery of video sources

2. **Workflow Worker** (`app/workflow_worker.py`)
   - Each workflow runs in a separate process
   - Uses `WorkflowExecutor` to execute node-based workflows
   - Reads frames from ring buffer and processes through workflow graph

3. **Video Ring Buffer** (`app/core/ringbuffer.py`)
   - Zero-copy frame sharing using POSIX shared memory
   - Thread-safe with atomic operations
   - Capacity = fps × duration (e.g., 10fps × 60s = 600 frames)
   - Supports `peek()` for non-destructive frame access

4. **Workflow Node System** (`app/core/workflow_types.py`)
   - **SourceNode**: Video source input (reads from ring buffer)
   - **AlgorithmNode**: AI detection algorithms (script-based plugins)
   - **FunctionNode**: Mathematical calculations (area ratios, distances, etc.)
   - **RoiDrawNode**: ROI configuration (passes to downstream algorithms)
   - **ConditionNode**: Conditional logic based on detection count
   - **AlertNode/OutputNode**: Alert generation and video recording

5. **Script Algorithm System** (`app/plugins/script_algorithm.py`)
   - Dynamic algorithm plugin loading from `app/user_scripts/`
   - All algorithms must implement `init(config)` and `process(frame, roi_regions, upstream_results)`
   - Supports multi-model YOLO detection with IoU grouping
   - Resource limiter with timeout and memory limits

6. **Web Interface** (`app/web/webapp.py`)
   - Flask-based UI for workflow/algorithm configuration
   - Visual workflow editor with node connections
   - ROI visual configuration interface
   - Alert history and monitoring

### Data Flow

```
RTSP Stream → Decoder → RingBuffer (Shared Memory) → Workflow Worker → Node Processing → Alert/Recording
                                                                             ↓
                                                                    [Source → Algo → Function → Alert]
```

**Key Design Patterns:**
- Node-based workflow: Flexible configuration through connected nodes
- Process isolation: Each workflow runs in separate process
- Shared memory: Zero-copy frame access via ring buffer
- Script plugins: User-defined detection logic in Python scripts

## Development Commands

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Initialize database
python app/setup_database.py

# Start the orchestrator (main entry point)
python app/main.py

# Start web interface (separate terminal)
python app/web/webapp.py
```

### Docker Development (Recommended)

```bash
# CPU version
docker build -f Dockerfile.cpu -t video-ba-pipe:cpu .
docker-compose up

# CUDA/GPU version
docker build -f Dockerfile.cuda -t video-ba-pipe:cuda .
docker-compose -f docker-compose.yml.cuda up

# View logs
docker logs video-ba-pipe-cpu -f
```

### Environment Configuration

Critical settings in `.env` (copy from `env.example`):

**Ring Buffer & Recording:**
- `RINGBUFFER_DURATION`: Buffer size in seconds (must be ≥ PRE_ALERT_DURATION)
- `RECORDING_FPS`: Target FPS for recording (5-15 recommended)
- `PRE_ALERT_DURATION` / `POST_ALERT_DURATION`: Recording window
- `IS_EXTREME_DECODE_MODE`: Skip intermediate frames for performance

**Alert System:**
- `ALERT_SUPPRESSION_DURATION`: Minimum time between same-type alerts
- `HEALTH_MONITOR_ENABLED`: Enable automatic source health checking
- `NO_FRAME_CRITICAL_THRESHOLD`: Seconds without frames before restart

**RabbitMQ:**
- `RABBITMQ_ENABLED`: Enable message queue publishing
- `RABBITMQ_HOST/PORT/USER/PASSWORD`: Connection settings

## Key Implementation Details

### Workflow Node Types

**Algorithm Node** - Runs AI detection:
```python
# In workflow JSON
{
  "id": "algo_1",
  "type": "algorithm",
  "data": {
    "dataId": 5,  # Algorithm ID from database
    "interval_seconds": 0.5,  # Optional: execution interval
    "config": {"roi_regions": [...]}  # Optional: override ROI
  }
}
```

**Function Node** - Multi-input calculations (e.g., area ratio between two detections):
```python
{
  "id": "func_1",
  "type": "function",
  "data": {
    "dataId": 10,  # Function script ID
    "config": {
      "function_name": "area_ratio",
      "threshold": 0.7,
      "operator": "less_than"
    }
  }
}
# Connect two algorithm nodes to this node
# Connections auto-identify as input A and input B
```

**ROI Draw Node** - Passes ROI configuration to downstream algorithms:
```python
{
  "id": "roi_1",
  "type": "roi_draw",
  "data": {
    "roi_regions": [{
      "name": "入口",
      "polygon": [[100, 100], [300, 100], [300, 300], [100, 300]],
      "mode": "post_filter"  // or "pre_mask"
    }]
  }
}
```

**Alert Node** - Generates alerts with optional window detection:
```python
{
  "id": "alert_1",
  "type": "alert",
  "data": {
    "alert_level": "warning",
    "alert_message": "检测到人员",
    "trigger_condition": {
      "enable": true,
      "window_size": 30,
      "mode": "ratio",
      "threshold": 0.3
    },
    "suppression": {
      "enable": true,
      "seconds": 60
    }
  }
}
```

### Script Algorithm Plugins

**Location:** `app/user_scripts/`

**Required Structure:**
```python
# metadata
SCRIPT_METADATA = {
    "name": "my_algorithm",
    "version": "1.0",
    "description": "My custom detection",
    "author": "Your Name",
    "options": []  # For UI configuration options
}

def init(config):
    """Initialize algorithm, load models"""
    # config contains models, script_config, ext_config
    return state  # Optional state object

def process(frame, roi_regions, state, upstream_results=None):
    """
    Process single frame

    Args:
        frame: numpy array (RGB format)
        roi_regions: list of ROI configs
        state: object returned from init()
        upstream_results: dict from connected nodes (for function nodes)

    Returns:
        {
            "detections": [
                {
                    "box": [x1, y1, x2, y2],
                    "label": "person",
                    "confidence": 0.95
                }
            ]
        }
    """
    pass
```

**Multi-Model Support:**
```python
# In init()
from app.core.yolo_detector import YOLODetector

model1 = YOLODetector(config['models'][0]['path'])
model2 = YOLODetector(config['models'][1]['path'])

# Use IoU grouping to combine results
from app.core.algorithm import BaseAlgorithm
detections = BaseAlgorithm.group_detections_by_iou(
    model1.detect(frame) + model2.detect(frame),
    iou_threshold=0.5
)
```

### ROI Integration

**Two Modes:**
1. **pre_mask**: Mask frame before detection (faster, less accurate)
   ```python
   mask = BaseAlgorithm.create_roi_mask(frame.shape, roi_regions)
   masked_frame = BaseAlgorithm.apply_roi_mask(frame, mask)
   detections = model.detect(masked_frame)
   ```

2. **post_filter**: Detect full frame, then filter (slower, more accurate)
   ```python
   detections = model.detect(frame)
   detections = BaseAlgorithm.filter_detections_by_roi(detections, roi_regions)
   ```

**ROI Configuration Priority:**
1. Context from upstream `roi_draw` node (highest)
2. Algorithm node `config.roi_regions`
3. Algorithm database default `roi_regions`

### Time Window Detection

The `WindowDetector` (`app/core/window_detector.py`) implements statistical alert verification:

**Configuration (per Alert node):**
```python
"trigger_condition": {
    "enable": True,
    "window_size": 30,  # seconds
    "mode": "ratio",  # or "consecutive" or "count"
    "threshold": 0.3  # 30% detection ratio
}
```

**Modes:**
- `ratio`: Detection ratio in window (detections / total frames)
- `consecutive`: Consecutive detections required
- `count`: Absolute count of detections in window

**Alert Suppression:**
```python
"suppression": {
    "enable": True,
    "seconds": 60  # Cooldown period after trigger
}
```

### Database Models (Peewee ORM)

**Core Tables:**
- `Algorithm`: AI algorithm configurations (script path, models)
- `VideoSource`: Video stream sources (RTSP URLs, decode config)
- `Workflow`: Workflow definitions (nodes and connections as JSON)
- `WorkflowNode`: Individual workflow nodes
- `WorkflowConnection`: Node connections
- `Alert`: Alert records with detection images and video paths
- `MLModel`: Uploaded model files
- `User`: User authentication

**Key Fields:**
```python
# Algorithm
script_path: str  # Path to user script
script_config: JSON  # Script-specific configuration
ext_config_json: JSON  # Execution config (timeout, memory)

# VideoSource
source_code: str  # Unique source identifier
source_url: str  # RTSP/HTTP/file path
source_decode_width/height: int  # Decode resolution
source_fps: int  # Target FPS

# Workflow
workflow_data: JSON  # {nodes: [...], connections: [...]}
```

### Working with Ring Buffers

**Access from Python:**
```python
from app.core.ringbuffer import VideoRingBuffer

# Connect to existing buffer
buffer = VideoRingBuffer(
    name=f"video_buffer.{source_code}",
    create=False,
    frame_shape=(height, width, 3),
    fps=10,
    duration_seconds=60
)

# Peek latest frame (non-destructive)
frame, timestamp = buffer.peek_with_timestamp(-1)

# Read frame (destructive, moves read pointer)
frame = buffer.read()

# Check health status
health = buffer.get_health_status()
# Returns: {
#   'frame_count': int,
#   'time_since_last_frame': float,
#   'consecutive_errors': int,
#   'is_healthy': bool
# }
```

**Buffer naming:** `video_buffer.{source_code}`

### Video Recording

Video recording is handled by alert/output nodes through the `VideoRecorder`:

**Configuration:**
- `PRE_ALERT_DURATION`: Seconds before alert to include
- `POST_ALERT_DURATION`: Seconds after alert to record
- `RECORDING_FPS`: Output video FPS
- `RINGBUFFER_DURATION`: Must be ≥ PRE_ALERT_DURATION

**Process:**
1. Alert triggered
2. Recorder reads `PRE_ALERT_DURATION` frames from ring buffer history
3. Continues recording for `POST_ALERT_DURATION`
4. Saves to `VIDEO_SAVE_PATH` with alert ID in filename
5. Non-blocking: runs in background thread

## Common Development Tasks

### Creating a New Workflow

1. Define workflow structure in Web UI or database
2. Add nodes (source → algorithm → function → alert)
3. Connect nodes with conditions
4. Activate workflow

**Example workflow JSON:**
```json
{
  "name": "人员检测",
  "workflow_data": {
    "nodes": [
      {"id": "source_1", "type": "source", "data": {"dataId": 1}},
      {"id": "roi_1", "type": "roi_draw", "data": {"roi_regions": [...] }},
      {"id": "algo_1", "type": "algorithm", "data": {"dataId": 5}},
      {"id": "alert_1", "type": "alert", "data": {"alert_level": "warning"}}
    ],
    "connections": [
      {"from": "source_1", "to": "roi_1"},
      {"from": "roi_1", "to": "algo_1"},
      {"from": "algo_1", "to": "alert_1", "condition": "detected"}
    ]
  }
}
```

### Adding a New Algorithm Script

1. Create script in `app/user_scripts/my_algorithm.py`
2. Implement `SCRIPT_METADATA`, `init()`, and `process()`
3. Add algorithm to database via Web UI
4. Configure models (upload via Web UI or specify paths)
5. Add to workflow as Algorithm node

**Example script:**
```python
SCRIPT_METADATA = {
    "name": "person_detector",
    "version": "1.0",
    "description": "YOLOv8 person detection",
    "author": "Your Name"
}

def init(config):
    from app.core.yolo_detector import YOLODetector
    model = YOLODetector(config['models'][0]['path'])
    return {'model': model}

def process(frame, roi_regions, state, upstream_results=None):
    model = state['model']
    detections = model.detect(frame)

    # Apply ROI if configured
    if roi_regions:
        from app.core.algorithm import BaseAlgorithm
        detections = BaseAlgorithm.filter_detections_by_roi(
            detections, roi_regions
        )

    return {'detections': detections}
```

### Debugging Pipeline Issues

**Check process status:**
```bash
docker ps  # or ps aux | grep python
```

**View logs:**
```bash
docker logs video-ba-pipe-cpu -f
# Filter for specific components
docker logs video-ba-pipe-cpu | grep -E "(WorkflowWorker|Orchestrator)"
```

**Common issues:**
- **Script not loading**: Check `script_path` in database, verify file exists in `app/user_scripts/`
- **High memory usage**: Reduce `RINGBUFFER_DURATION` or `RECORDING_FPS`
- **No detections**: Check ROI configuration, model paths, confidence thresholds
- **Workflow not starting**: Check workflow JSON validity, node connections
- **Video recording issues**: Verify `RINGBUFFER_DURATION >= PRE_ALERT_DURATION`

### Testing ROI Configuration

1. Configure ROI via Web UI workflow editor
2. Add `roi_draw` node before algorithm node
3. Check logs for ROI mask application
4. Verify detections only in configured zones

**Log output:**
```
[WorkflowWorker] 热区绘制节点 roi_1 已记录ROI信息: 位置(100,100), 尺寸(200x200)
[WorkflowWorker] 算法节点 algo_1 使用context中的ROI配置，包含 1 个区域
```

### Working with Function Nodes

Function nodes enable multi-input calculations:

**Single-input functions** (one algorithm node):
- `height_ratio_frame`: Detection height / frame height
- `width_ratio_frame`: Detection width / frame width
- `area_ratio_frame`: Detection area / frame area
- `size_absolute`: Absolute pixel size (height/width/area)

**Multi-input functions** (two algorithm nodes):
- `area_ratio`: Area of detection A / area of detection B
- `height_ratio`: Height of A / height of B
- `iou_check`: Intersection over Union
- `distance_check`: Distance between detections

**Setup:**
1. Add algorithm nodes to workflow
2. Add function node
3. Connect algorithm nodes to function node (first = input A, second = input B)
4. Configure function type, thresholds, and class filters

## File Structure Notes

- `app/core/decoder/`: FFmpeg decoder implementations (async, NVDEC, VideoToolbox)
- `app/core/streamer.py`: Stream protocol handlers (RTSP, HTTP-FLV, HLS)
- `app/user_scripts/templates/`: Script templates for different node types
- `app/user_scripts/examples/`: Example algorithm implementations
- `docs/`: Additional documentation (ROI guide, function node guide, etc.)
- `scripts/`: Utility scripts for Docker plugin fixes, RabbitMQ testing

## Important Constraints

1. **Process Isolation**: Each workflow runs in separate process - use shared memory for cross-process data
2. **Frame Format**: Ring buffer outputs RGB format; convert to BGR for OpenCV operations
3. **Coordinate Systems**: ROI coordinates use decoded resolution (configurable per VideoSource)
4. **Thread Safety**: Ring buffer uses atomic operations, but avoid concurrent `read()` calls
5. **Memory Management**: Always `buffer.close()` and `buffer.unlink()` when done
6. **Script Resources**: Scripts have timeout and memory limits (configurable per algorithm)
7. **Workflow Connections**: Conditions on connections control flow ("detected", "always", etc.)

## Performance Tuning

**For high-throughput scenarios:**
- Use NVDEC decoder (CUDA build)
- Enable `IS_EXTREME_DECODE_MODE` (skip intermediate frames)
- Reduce `RECORDING_FPS` to 5-10 fps
- Keep `RINGBUFFER_DURATION` minimal (≥ PRE_ALERT_DURATION)
- Use ROI to reduce false positives

**For accuracy:**
- Increase `RECORDING_FPS` to 15 fps
- Use `post_filter` ROI mode (higher precision)
- Tune `window_threshold` based on false positive rate
- Enable multi-model detection with IoU grouping