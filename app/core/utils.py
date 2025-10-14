import os

from collections import defaultdict

def save_frame(frame_data, save_path: str):
    import cv2
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    frame_data = frame_data.copy()
    frame_data = cv2.cvtColor(frame_data, cv2.COLOR_RGB2BGR)
    cv2.imwrite(save_path, frame_data)




# calculate_iou 函数与之前相同，这里省略以保持简洁
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


def find_multimodel_groups(stages_results, iou_threshold=0.5):
    """
    在多个模型的检测结果中寻找一个目标集合，该集合必须包含来自所有模型的重叠检测框。

    参数:
    - stages_results (dict): 键是模型名称，值是YOLO的Results对象.
    - iou_threshold (float): IoU阈值.

    返回:
    - list: 一个列表，其中每个元素是一个“有效组”，代表一个被所有模型共同检测到的目标区域。
            每个“有效组”是一个列表，包含所有相关的检测框信息。
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

        boxes = results_result.boxes
        class_names = results_result.names
        for i in range(len(boxes)):
            all_boxes_with_info.append({
                'model_name': model_name,
                'class_name': class_names[int(boxes.cls[i])],
                'confidence': float(boxes.conf[i]),
                'bbox': boxes.xyxy[i].tolist()
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

            # 仅当模型不同且IoU大于阈值时，才连接边
            if box1_info['model_name'] != box2_info['model_name']:
                if calculate_iou(box1_info['bbox'], box2_info['bbox']) > iou_threshold:
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

