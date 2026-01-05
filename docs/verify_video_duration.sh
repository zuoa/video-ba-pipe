#!/bin/bash
# 验证视频录制时长是否正确

echo "======================================"
echo "视频时长验证脚本"
echo "======================================"
echo ""

VIDEO_DIR="app/data/videos"

# 读取配置
PRE_DURATION=${PRE_ALERT_DURATION:-10}
POST_DURATION=${POST_ALERT_DURATION:-10}
FPS=${RECORDING_FPS:-5}

EXPECTED_DURATION=$((PRE_DURATION + POST_DURATION))
EXPECTED_FRAMES=$((EXPECTED_DURATION * FPS))

echo "配置信息："
echo "  前录制时长: ${PRE_DURATION}秒"
echo "  后录制时长: ${POST_DURATION}秒"
echo "  录制帧率: ${FPS}fps"
echo "  期望时长: ${EXPECTED_DURATION}秒"
echo "  期望帧数: ${EXPECTED_FRAMES}帧"
echo ""
echo "======================================"
echo ""

# 查找最新的5个视频文件
echo "检查最新的视频文件..."
echo ""

count=0
total_pass=0
total_fail=0

while IFS= read -r video_file; do
    count=$((count + 1))
    
    if [ $count -gt 5 ]; then
        break
    fi
    
    echo "[$count] 文件: $(basename "$video_file")"
    
    # 获取视频时长
    duration=$(ffprobe -v error -show_entries format=duration \
        -of default=noprint_wrappers=1:nokey=1 "$video_file" 2>/dev/null)
    
    if [ -z "$duration" ]; then
        echo "    ⚠️  无法读取视频信息"
        continue
    fi
    
    # 获取帧数和帧率
    frame_info=$(ffprobe -v error -count_frames -select_streams v:0 \
        -show_entries stream=nb_read_frames,r_frame_rate \
        -of default=noprint_wrappers=1 "$video_file" 2>/dev/null)
    
    frame_rate=$(echo "$frame_info" | grep "r_frame_rate" | cut -d'=' -f2 | awk -F'/' '{print $1/$2}')
    frame_count=$(echo "$frame_info" | grep "nb_read_frames" | cut -d'=' -f2)
    
    # 计算百分比
    duration_int=${duration%.*}
    duration_percent=$((duration_int * 100 / EXPECTED_DURATION))
    frame_percent=$((frame_count * 100 / EXPECTED_FRAMES))
    
    echo "    时长: ${duration}秒 (期望: ${EXPECTED_DURATION}秒, ${duration_percent}%)"
    echo "    帧数: ${frame_count}帧 (期望: ${EXPECTED_FRAMES}帧, ${frame_percent}%)"
    echo "    帧率: ${frame_rate}fps"
    
    # 判断是否通过（允许±20%误差）
    if [ $duration_percent -ge 80 ] && [ $duration_percent -le 120 ] && \
       [ $frame_percent -ge 80 ] && [ $frame_percent -le 120 ]; then
        echo "    ✅ 通过"
        total_pass=$((total_pass + 1))
    else
        echo "    ❌ 失败：时长或帧数不符合预期"
        total_fail=$((total_fail + 1))
    fi
    
    echo ""
    
done < <(find "$VIDEO_DIR" -type f -name "*.mp4" -print0 | xargs -0 ls -t)

echo "======================================"
echo "验证结果："
echo "  通过: $total_pass 个"
echo "  失败: $total_fail 个"
echo "======================================"
echo ""

if [ $total_fail -eq 0 ] && [ $total_pass -gt 0 ]; then
    echo "✅ 所有视频时长正常！"
    exit 0
elif [ $total_pass -eq 0 ]; then
    echo "❌ 所有视频时长都不正常，请检查："
    echo "   1. 是否已重启应用？"
    echo "   2. 检查的视频是否是修复后录制的？"
    echo "   3. 查看日志: tail -f app/data/logs/debug.log | grep 录制"
    exit 1
else
    echo "⚠️  部分视频时长正常，部分不正常"
    echo "   旧视频可能是修复前录制的，请触发新的告警录制"
    exit 0
fi

