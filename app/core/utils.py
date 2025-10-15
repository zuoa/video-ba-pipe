import os

from collections import defaultdict

def save_frame(frame_data, save_path: str):
    import cv2
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    frame_data = frame_data.copy()
    frame_data = cv2.cvtColor(frame_data, cv2.COLOR_RGB2BGR)
    cv2.imwrite(save_path, frame_data)




def expand_box(box, width_expand_ratio, height_expand_ratio):
    """
    按比例放大检测框，宽度和高度可以设置不同的放大比例
    
    参数:
    - box: [x1, y1, x2, y2] 格式的边界框
    - width_expand_ratio: 宽度放大比例，如0.2表示放大20%
    - height_expand_ratio: 高度放大比例，如0.2表示放大20%
    
    返回:
    - 放大后的边界框 [x1, y1, x2, y2]
    """
    x1, y1, x2, y2 = box
    width = x2 - x1
    height = y2 - y1
    
    # 计算扩大的像素数
    expand_width = width * width_expand_ratio
    expand_height = height * height_expand_ratio
    
    # 扩大边界框
    new_x1 = max(0, x1 - expand_width / 2)
    new_y1 = max(0, y1 - expand_height / 2)
    new_x2 = x2 + expand_width / 2
    new_y2 = y2 + expand_height / 2
    
    return [new_x1, new_y1, new_x2, new_y2]


def calculate_iou(box1, box2):
    """计算两个边界框的IoU (Intersection over Union)."""
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])
    intersection_area = max(0, x2 - x1) * max(0, y2 - y1)
    box1_area = (box1[2] - box1[0]) * (box1[3] - box1[1])
    box2_area = (box2[2] - box2[0]) * (box2[3] - box2[1])
    union_area = box1_area + box2_area - intersection_area
    return intersection_area / union_area if union_area > 0 else 0


def boxes_intersect(box1, box2):
    """
    判断两个边界框是否有重叠（包括交叉、包含等所有重叠情况）
    
    该函数会检测以下所有情况：
    1. 两个框部分交叉
    2. 一个框完全包含另一个框
    3. 两个框完全重合
    
    参数:
    - box1, box2: [x1, y1, x2, y2] 格式的边界框
    
    返回:
    - bool: 如果两个框有任何重叠返回True，否则返回False
    """
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])
    
    # 如果有重叠（交叉或包含），交集区域的宽度和高度都应该大于0
    return x2 > x1 and y2 > y1


def find_multimodel_groups(stages_results):
    """
    在多个模型的检测结果中寻找一个目标集合，该集合必须包含来自所有模型的重叠检测框。
    检测框会先按每个模型配置中的宽度和高度比例放大，然后判断是否有重叠（包括交叉、包含等所有情况）。

    参数:
    - stages_results (dict): 键是模型名称，值是包含'result'和'model_config'的字典.
                            model_config中应包含'expand_width'和'expand_height'字段.

    返回:
    - list: 一个列表，其中每个元素是一个"有效组"，代表一个被所有模型共同检测到的目标区域。
            每个"有效组"是一个列表，包含所有相关的检测框信息。
            e.g., [
                [  # 第一个有效组
                    {'model_name': 'person_model', ...},
                    {'model_name': 'head_model', ...},
                    {'model_name': 'hand_model', ...}
                ],
                ...
            ]
    """
    # 1. 提取并整合所有模型的检测框信息
    all_boxes_with_info = []
    if not stages_results:
        return []


    for model_name, results in stages_results.items():

        results_result = results.get('result')
        model_config = results.get('model_config', {})
        
        # 获取该模型的expand配置，如果没有则使用默认值
        expand_width = model_config.get('expand_width', 0.1)
        expand_height = model_config.get('expand_height', 0.1)

        boxes = results_result.boxes
        class_names = results_result.names
        for i in range(len(boxes)):
            all_boxes_with_info.append({
                'model_name': model_name,
                'class_name': class_names[int(boxes.cls[i])],
                'class': int(boxes.cls[i]),
                'confidence': float(boxes.conf[i]),
                'bbox': boxes.xyxy[i].tolist(),
                'expand_width': expand_width,
                'expand_height': expand_height
            })

    num_boxes = len(all_boxes_with_info)
    if num_boxes == 0:
        return []

    # 2. 构建邻接表来表示图
    adj = defaultdict(list)
    for i in range(num_boxes):
        for j in range(i + 1, num_boxes):
            box1_info = all_boxes_with_info[i]
            box2_info = all_boxes_with_info[j]

            # 仅当模型不同时，才检查重叠
            if box1_info['model_name'] != box2_info['model_name']:
                # 将检测框按各自模型配置的宽度和高度比例放大
                expanded_box1 = expand_box(box1_info['bbox'], 
                                          box1_info['expand_width'], 
                                          box1_info['expand_height'])
                expanded_box2 = expand_box(box2_info['bbox'], 
                                          box2_info['expand_width'], 
                                          box2_info['expand_height'])
                
                # 判断放大后的框是否有重叠（包括交叉、包含等所有情况）
                if boxes_intersect(expanded_box1, expanded_box2):
                    adj[i].append(j)
                    adj[j].append(i)

    # 3. 使用深度优先搜索(DFS)寻找所有连通分量（碰撞簇）
    visited = set()
    clusters = []
    for i in range(num_boxes):
        if i not in visited:
            current_cluster_indices = []
            stack = [i]
            visited.add(i)
            while stack:
                node_idx = stack.pop()
                current_cluster_indices.append(node_idx)
                for neighbor_idx in adj[node_idx]:
                    if neighbor_idx not in visited:
                        visited.add(neighbor_idx)
                        stack.append(neighbor_idx)
            clusters.append([all_boxes_with_info[k] for k in current_cluster_indices])

    # 4. 筛选出包含所有模型检测结果的簇
    required_models = set(stages_results.keys())
    valid_groups = []
    for cluster in clusters:
        models_in_cluster = {box['model_name'] for box in cluster}
        # 检查当前簇中的模型集合是否与要求的所有模型集合完全相同
        if models_in_cluster == required_models:
            valid_groups.append(cluster)

    return valid_groups

