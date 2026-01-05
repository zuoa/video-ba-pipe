"""
Hook脚本模板

Hook在特定时机执行，可以修改frame或检测结果
"""

# 脚本元数据
SCRIPT_METADATA = {
    'name': 'my_hook',
    'version': '1.0.0',
    'author': 'Your Name',
    'description': 'Description',
    'hook_point': 'pre_detect',  # Hook点: 'pre_detect', 'post_detect', 'pre_alert', 'pre_record', 'post_record'
    'priority': 100,  # 优先级（越小越先执行）
    'dependencies': []
}


def execute(context):
    """
    Hook执行函数

    Args:
        context: dict, 包含不同的字段取决于hook_point：

        pre_detect:
            - frame: numpy.ndarray, 输入帧（可修改）
            - task_id: int, 任务ID
            - algorithm_id: int, 算法ID
            - hook_point: str, Hook点名称

        post_detect:
            - detections: list, 检测结果列表（可修改）
            - frame: numpy.ndarray, 原始帧
            - task_id: int
            - algorithm_id: int
            - detection_count: int, 检测数量
            - hook_point: str

    Returns:
        dict: {
            'frame': modified_frame,    # 修改后的frame (仅pre_detect)
            'detections': modified_detections,  # 修改后的detections (仅post_detect)
            'metadata': {},             # 额外信息
            'skip': False               # 是否跳过后续处理
        }
    """
    hook_point = context.get('hook_point', 'pre_detect')

    if hook_point == 'pre_detect':
        # ===== Pre-Detect Hook: 可以修改frame =====

        frame = context['frame']

        # 示例：图像增强
        # import cv2
        # enhanced = cv2.convertScaleAbs(frame, alpha=1.2, beta=30)

        # 示例：调整尺寸
        # height, width = frame.shape[:2]
        # if width > 1920:
        #     scale = 1920 / width
        #     enhanced = cv2.resize(frame, (0, 0), fx=scale, fy=scale)

        # 示例：裁剪区域
        # enhanced = frame[100:900, 200:1600]

        # 示例：检查条件，决定是否跳过
        # brightness = frame.mean()
        # if brightness < 10:
        #     return {'skip': True, 'metadata': {'reason': 'too dark'}}

        return {
            'frame': frame,  # 返回修改后的frame
            'metadata': {},
            'skip': False
        }

    elif hook_point == 'post_detect':
        # ===== Post-Detect Hook: 可以过滤/增强detections =====

        detections = context['detections']
        frame = context['frame']

        # 示例：按置信度过滤
        # filtered = [d for d in detections if d['confidence'] > 0.8]

        # 示例：按区域过滤
        # filtered = []
        # for d in detections:
        #     x1, y1, x2, y2 = d['box']
        #     # 只保留中心区域的检测
        #     cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        #     if 500 < cx < 1500 and 200 < cy < 800:
        #         filtered.append(d)

        # 示例：添加额外信息
        # for d in detections:
        #     d['metadata']['area'] = (d['box'][2] - d['box'][0]) * (d['box'][3] - d['box'][1])

        # 示例：NMS去重
        # import cv2
        # boxes = np.array([d['box'] for d in detections])
        # scores = np.array([d['confidence'] for d in detections])
        # indices = cv2.dnn.NMSBoxes(boxes.tolist(), scores.tolist(), 0.5, 0.4)
        # filtered = [detections[i[0]] for i in indices]

        filtered = detections

        return {
            'detections': filtered,
            'metadata': {
                'original_count': len(detections),
                'filtered_count': len(filtered)
            },
            'skip': False
        }

    return {
        'metadata': {},
        'skip': False
    }
