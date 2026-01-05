#!/bin/bash

# 插件加载验证脚本
# 检查容器中插件是否正常加载

set -e

CONTAINER_NAME="${1:-video-ba-pipe-cpu}"
API_PORT="${2:-5001}"

echo "=========================================="
echo "插件加载状态验证"
echo "=========================================="
echo ""

# 检查容器是否运行
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "❌ 错误: 容器 '${CONTAINER_NAME}' 未运行"
    exit 1
fi

echo "目标容器: ${CONTAINER_NAME}"
echo "API 端口: ${API_PORT}"
echo ""

# 等待服务启动
echo "等待服务启动..."
sleep 5

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "1. 检查容器日志中的插件加载信息"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# 检查日志中的插件加载信息
if docker logs "${CONTAINER_NAME}" 2>&1 | grep -q "发现算法插件.*target_detection"; then
    echo "✅ 在容器日志中找到 target_detection 插件加载记录"
    docker logs "${CONTAINER_NAME}" 2>&1 | grep "发现算法插件.*target_detection" | tail -1
else
    echo "⚠️  未在容器日志中找到插件加载记录"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "2. 测试 API 端点 - 获取插件模块列表"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# 测试 API
API_RESPONSE=$(curl -s "http://localhost:${API_PORT}/api/plugins/modules" || echo '{"error": "无法连接到API"}')
echo "API 响应:"
echo "${API_RESPONSE}" | python3 -m json.tool 2>/dev/null || echo "${API_RESPONSE}"

if echo "${API_RESPONSE}" | grep -q "target_detection"; then
    echo ""
    echo "✅ API 可以正确列出 target_detection 插件"
else
    echo ""
    echo "❌ API 未能列出 target_detection 插件"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "3. 检查插件文件是否存在于容器中"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if docker exec "${CONTAINER_NAME}" test -f /app/app/plugins/target_detection.py; then
    echo "✅ 插件文件 /app/app/plugins/target_detection.py 存在"
    
    # 检查文件内容
    if docker exec "${CONTAINER_NAME}" grep -q "class TargetDetector" /app/app/plugins/target_detection.py; then
        echo "✅ 插件类 TargetDetector 定义存在"
    else
        echo "⚠️  未找到 TargetDetector 类定义"
    fi
else
    echo "❌ 插件文件不存在"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "4. 检查 plugin_manager.py 版本"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if docker exec "${CONTAINER_NAME}" grep -q "找到最后一个'app'的索引" /app/app/plugin_manager.py; then
    echo "✅ plugin_manager.py 已更新为修复版本"
else
    echo "⚠️  plugin_manager.py 可能不是修复版本"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "5. 检查 Python 模块导入"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# 尝试导入模块
IMPORT_TEST=$(docker exec "${CONTAINER_NAME}" python3 -c "
import sys
sys.path.insert(0, '/app')
try:
    from app.plugins.target_detection import TargetDetector
    print('✅ 成功导入 TargetDetector 类')
    print('模块名:', TargetDetector.__module__)
    instance = TargetDetector({'models': []})
    print('算法名:', instance.name)
except Exception as e:
    print('❌ 导入失败:', str(e))
" 2>&1)

echo "${IMPORT_TEST}"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "验证完成"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# 总结
if echo "${API_RESPONSE}" | grep -q "target_detection" && \
   echo "${IMPORT_TEST}" | grep -q "成功导入"; then
    echo "🎉 插件加载验证成功！"
    exit 0
else
    echo "⚠️  插件加载可能存在问题，请查看上述详细信息"
    echo ""
    echo "建议操作："
    echo "1. 查看完整日志: docker logs ${CONTAINER_NAME}"
    echo "2. 进入容器调试: docker exec -it ${CONTAINER_NAME} /bin/bash"
    echo "3. 重新应用修复: make fix-plugin-cpu (或 fix-plugin-cuda)"
    exit 1
fi

