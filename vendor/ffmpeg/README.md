Place prebuilt RK3588/arm64 FFmpeg packages here when building `Dockerfile.ffmpeg.rk`.

This directory contains a `.keep` placeholder so Docker builds still work when no
archive has been checked in yet.

Recommended contents:
- a tar archive that extracts to `bin/ffmpeg`, `bin/ffprobe`, and optional `lib/`
- built with Rockchip `rkmpp` support

Supported filenames:
- `*.tar.gz`
- `*.tgz`
- `*.tar.xz`
- `*.tar`

Priority order during `Dockerfile.ffmpeg.rk` build:
1. `--build-arg FFMPEG_RK_PACKAGE=<https-url>`
2. first archive found in this directory
3. Debian `apt` ffmpeg fallback (software decode only)
