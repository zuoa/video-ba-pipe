# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Video BA Pipe is a video stream analysis system that processes RTSP/video streams, applies AI detection algorithms (YOLO-based), and generates alerts with video recording capabilities. The system uses a multi-process architecture with shared memory buffers for efficient video frame processing.

**Core Features:**
- Real-time video stream processing (RTSP, HTTP-FLV, HLS, local files)
- Multi-algorithm detection with plugin architecture
- Time-window based alert detection (prevents false alarms)
- ROI (Region of Interest) hot-zone configuration
- Video recording with pre/post alert buffering
- RabbitMQ integration for alert publishing
- Web UI for configuration and monitoring

## Architecture

The system uses a **multi-process orchestrator pattern**:

### Main Components

1. **Orchestrator** (`app/core/orchestrator.py`)
   - Manages task lifecycle (start/stop/restart)
   - Spawns and monitors decoder and AI worker processes
   - Creates shared memory ring buffers for each task
   - Performs health checks and automatic recovery

2. **Decoder Worker** (`app/decoder_worker.py`)
   - Connects to video stream sources (RTSP, HTTP-FLV, HLS, files)
   - Decodes frames using FFmpeg (software/hardware/NVDEC)
   - Writes frames to shared memory ring buffer at target FPS
   - Supports multiple sampling modes (all/interval/fps)

3. **AI Worker** (`app/ai_worker.py`)
   - Reads frames from ring buffer using `peek()` (non-destructive)
   - Runs multiple detection algorithms concurrently via ThreadPoolExecutor
   - Implements time-window detection logic
   - Triggers alerts, saves detection images, and starts video recording

4. **Plugin System** (`app/plugin_manager.py`)
   - Dynamic algorithm plugin loading from `app/plugins/`
   - Hot-reload support for development
   - All plugins must inherit from `BaseAlgorithm` (`app/core/algorithm.py`)

5. **Web Interface** (`app/web/webapp.py`)
   - Flask-based UI for task/algorithm configuration
   - ROI visual configuration interface
   - Alert history and monitoring

### Data Flow

```
RTSP Stream → Decoder Worker → RingBuffer (Shared Memory) → AI Worker → Detection → Alert/Recording
                                    ↑
                                 (peek)
                                    ↓
                              Video Recorder (background thread)
```

**Key Design Pattern:** The AI Worker uses `buffer.peek()` instead of `buffer.read()` to avoid consuming frames, allowing the Video Recorder to access historical frames for pre-alert recording.

### Ring Buffer System

The `VideoRingBuffer` (`app/core/ringbuffer.py`) uses POSIX shared memory:
- Zero-copy frame sharing between decoder and AI workers
- Thread-safe with atomic operations
- Capacity = fps × duration (e.g., 10fps × 60s = 600 frames)
- Maintains frame timestamps for window detection

## Development Commands

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run database migrations
python app/migrate_add_roi.py

# Start the orchestrator (main entry point)
python app/main.py

# Start web interface (separate terminal)
python app/web/webapp.py

# Run tests
make test
make test-coverage
```

### Docker Development (Recommended)

```bash
# CPU version
make build-cpu          # Build CPU image
make up-cpu             # Start CPU services
make logs-cpu           # View logs
make shell-cpu          # Enter container

# CUDA/GPU version
make gpu-check          # Verify GPU availability
make build-cuda         # Build CUDA image
make up-cuda            # Start CUDA services
make logs-cuda          # View logs
make monitor-cuda       # Monitor GPU usage

# Common operations
make ps-cpu / ps-cuda   # Check service status
make down-cpu / down-cuda  # Stop services
make rebuild-cpu / rebuild-cuda  # Rebuild and restart

# Plugin hot-fix (if plugins not loading)
make fix-plugin-cpu     # Fix plugin loading in container
make verify-plugin-cpu  # Verify plugin status
```

### Environment Configuration

Copy `env.example` to `.env` and configure:

**Critical Settings:**
- `RINGBUFFER_DURATION`: Buffer size in seconds (must be ≥ PRE_ALERT_DURATION)
- `RECORDING_FPS`: Target FPS for recording (5-15 recommended)
- `PRE_ALERT_DURATION` / `POST_ALERT_DURATION`: Recording window
- `ALERT_SUPPRESSION_DURATION`: Minimum time between same-type alerts
- `RABBITMQ_ENABLED`: Enable message queue publishing

## Key Implementation Details

### Algorithm Plugins

**Location:** `app/plugins/`

**Structure:**
```python
from app.core.algorithm import BaseAlgorithm

class MyAlgorithm(BaseAlgorithm):
    @property
    def name(self) -> str:
        return "algorithm_name"  # Must match database

    def load_model(self):
        # Load models from self.config['models_config']
        pass

    def process(self, frame: np.ndarray, roi_regions: list = None) -> dict:
        # Return: {'detections': [{'box': [x1,y1,x2,y2], 'label': '...', 'confidence': 0.9}]}
        pass
```

**Multi-Model Support:** Algorithms can use multiple YOLO models. The `TargetDetector` plugin implements intersection-over-union (IoU) grouping to combine results from multiple models.

**ROI Integration:**
- ROI configuration is passed via `roi_regions` parameter
- Use `create_roi_mask()` → `apply_roi_mask()` → `filter_detections_by_roi()`
- Two modes: `pre_mask` (mask before detection) vs `post_filter` (filter after detection)

### Time Window Detection

The `WindowDetector` (`app/core/window_detector.py`) implements statistical alert verification:

**Configuration (per algorithm):**
- `enable_window_check`: Enable/disable feature
- `window_size`: Time window in seconds
- `window_mode`: 'ratio' (detection ratio) or 'consecutive' (consecutive detections)
- `window_threshold`: Threshold value

**Flow:**
1. AI Worker records every frame result to in-memory deque
2. When detection occurs, check if window condition is met
3. Only trigger alert if condition passes AND suppression period expired

### Database Models (Peewee ORM)

**Core Tables:**
- `Task`: Video source and processing configuration
- `Algorithm`: Algorithm plugin and model configuration
- `TaskAlgorithm`: Junction table with ROI configuration (`roi_regions` JSON field)
- `Alert`: Alert records with detection images and video paths

**Migration:** Use provided scripts in `app/` directory (e.g., `migrate_add_roi.py`)

### Video Recording

The `VideoRecorder` (`app/core/video_recorder.py`) runs background threads:
- Reads frames from ring buffer for `PRE_ALERT_DURATION + POST_ALERT_DURATION`
- Automatically stitches historical + new frames
- Saves to `VIDEO_SAVE_PATH` with alert ID in filename
- Non-blocking: returns immediately after starting recording

## Common Development Tasks

### Adding a New Algorithm

1. Create plugin in `app/plugins/my_algorithm.py`
2. Implement `BaseAlgorithm` interface
3. Add to database via Web UI or direct DB insertion
4. Associate with Task via Web UI
5. Configure ROI hot-zones (optional)

### Debugging Pipeline Issues

**Check process status:**
```bash
make ps-cpu  # or ps-cuda
```

**View logs:**
```bash
make logs-cpu | grep -E "(AIWorker|DecoderWorker|Orchestrator)"
```

**Common issues:**
- **Plugin not loading**: Run `make fix-plugin-cpu`, check module path in `plugin_manager.py`
- **High memory usage**: Reduce `RINGBUFFER_DURATION` or `RECORDING_FPS`
- **No detections**: Check ROI configuration, algorithm confidence thresholds, window settings
- **Video recording issues**: Verify `RINGBUFFER_DURATION >= PRE_ALERT_DURATION`

### Working with Ring Buffers

**Access buffer from Python (for debugging):**
```python
from app.core.ringbuffer import VideoRingBuffer

# Connect to existing buffer
buffer = VideoRingBuffer(
    name="buffer_name.1",
    create=False,
    frame_shape=(1080, 1920, 3),
    fps=10,
    duration_seconds=60
)

# Peek latest frame (non-destructive)
frame, timestamp = buffer.peek_with_timestamp(-1)

# Read frame (destructive, moves read pointer)
frame = buffer.read()
```

**Buffer naming convention:** `{task.buffer_name}.{task.id}`

### Testing ROI Configuration

1. Configure ROI via Web UI at `/roi-config`
2. Enable window detection debug logging
3. Run AI Worker, check logs for ROI mask application
4. Verify detections only trigger in configured zones

## File Structure Notes

- `app/core/decoder/`: FFmpeg decoder implementations (async, NVDEC, VideoToolbox)
- `app/core/streamer.py`: Stream protocol handlers (RTSP, HTTP-FLV, HLS)
- `app/core/pipe.py`: Legacy pipeline code (being phased out)
- `docs/`: Additional documentation
- `scripts/`: Utility scripts for Docker plugin fixes

## Important Constraints

1. **Process Isolation:** Decoder and AI workers run in separate processes - use shared memory for data exchange
2. **Frame Format:** Decoders output RGB format, convert to BGR for OpenCV operations
3. **Coordinate Systems:** ROI coordinates use decoded resolution (configurable per task)
4. **Thread Safety:** Ring buffer uses atomic operations, but avoid concurrent `read()` calls
5. **Memory Management:** Always `buffer.close()` and `buffer.unlink()` when done to prevent leaks

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