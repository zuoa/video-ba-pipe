#!/bin/sh

set -eu

sample_path="${1:-}"
sample_codec="${2:-h264}"

echo "Checking Jetson release..."
if [ -r /etc/nv_tegra_release ]; then
    head -n 1 /etc/nv_tegra_release
else
    echo "/etc/nv_tegra_release is not visible; check the NVIDIA container runtime" >&2
    exit 1
fi

echo "Checking CUDA-enabled PyTorch..."
python3 -c "import torch; assert torch.cuda.is_available(), 'CUDA is unavailable'; print('torch=' + torch.__version__); print('device=' + torch.cuda.get_device_name(0))"

echo "Checking Jetson GStreamer plugins..."
gst-inspect-1.0 nvv4l2decoder >/dev/null
gst-inspect-1.0 nvvidconv >/dev/null

echo "Checking application decoder import..."
python3 -c "from app.core.decoder.jetson import JetsonGStreamerDecoder; print(JetsonGStreamerDecoder.__name__)"

if [ -n "${sample_path}" ]; then
    case "${sample_codec}" in
        h264)
            parser="h264parse"
            ;;
        h265|hevc)
            parser="h265parse"
            ;;
        *)
            echo "Unsupported sample codec: ${sample_codec}; use h264 or h265" >&2
            exit 1
            ;;
    esac
    if [ ! -f "${sample_path}" ]; then
        echo "Sample bitstream not found: ${sample_path}" >&2
        exit 1
    fi
    echo "Decoding sample with nvv4l2decoder..."
    gst-launch-1.0 -q filesrc location="${sample_path}" \
        ! "${parser}" ! nvv4l2decoder ! nvvidconv ! video/x-raw,format=NV12 ! fakesink
else
    echo "No sample supplied; plugin discovery passed, decode smoke test skipped."
fi

echo "Jetson runtime verification passed."
