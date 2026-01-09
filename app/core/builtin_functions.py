from typing import List, Dict, Any, Tuple, Optional


def get_box_area(box: Tuple[float, float, float, float]) -> float:
    x1, y1, x2, y2 = box
    return (x2 - x1) * (y2 - y1)


def get_box_height(box: Tuple[float, float, float, float]) -> float:
    x1, y1, x2, y2 = box
    return y2 - y1


def get_box_width(box: Tuple[float, float, float, float]) -> float:
    x1, y1, x2, y2 = box
    return x2 - x1


def get_box_center(box: Tuple[float, float, float, float]) -> Tuple[float, float]:
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def calculate_iou(box1: Tuple[float, float, float, float], 
                  box2: Tuple[float, float, float, float]) -> float:
    x1_1, y1_1, x2_1, y2_1 = box1
    x1_2, y1_2, x2_2, y2_2 = box2
    
    inter_x1 = max(x1_1, x1_2)
    inter_y1 = max(y1_1, y1_2)
    inter_x2 = min(x2_1, x2_2)
    inter_y2 = min(y2_1, y2_2)
    
    if inter_x2 < inter_x1 or inter_y2 < inter_y1:
        return 0.0
    
    inter_area = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
    area1 = get_box_area(box1)
    area2 = get_box_area(box2)
    union_area = area1 + area2 - inter_area
    
    return inter_area / union_area if union_area > 0 else 0.0


def calculate_distance(box1: Tuple[float, float, float, float], 
                       box2: Tuple[float, float, float, float]) -> float:
    c1 = get_box_center(box1)
    c2 = get_box_center(box2)
    return ((c1[0] - c2[0])**2 + (c1[1] - c2[1])**2)**0.5


class BuiltinFunction:
    
    @staticmethod
    def area_ratio(detections_a: List[Dict], detections_b: List[Dict], 
                   config: Dict) -> List[Dict]:
        threshold = config.get('threshold', 0.7)
        operator = config.get('operator', 'less_than')
        
        results = []
        for det_a in detections_a:
            for det_b in detections_b:
                area_a = get_box_area(det_a['box'])
                area_b = get_box_area(det_b['box'])
                
                if area_b == 0:
                    continue
                
                ratio = area_a / area_b
                
                matched = False
                if operator == 'less_than':
                    matched = ratio < threshold
                elif operator == 'greater_than':
                    matched = ratio > threshold
                elif operator == 'equal':
                    matched = abs(ratio - threshold) < 0.01
                
                if matched:
                    results.append({
                        'object_a': det_a,
                        'object_b': det_b,
                        'ratio': ratio,
                        'threshold': threshold,
                        'matched': True,
                        'function': 'area_ratio'
                    })
        
        return results
    
    @staticmethod
    def height_ratio(detections_a: List[Dict], detections_b: List[Dict], 
                     config: Dict) -> List[Dict]:
        threshold = config.get('threshold', 0.3)
        operator = config.get('operator', 'greater_than')
        
        results = []
        for det_a in detections_a:
            for det_b in detections_b:
                height_a = get_box_height(det_a['box'])
                height_b = get_box_height(det_b['box'])
                
                if height_b == 0:
                    continue
                
                ratio = height_a / height_b
                
                matched = False
                if operator == 'less_than':
                    matched = ratio < threshold
                elif operator == 'greater_than':
                    matched = ratio > threshold
                elif operator == 'equal':
                    matched = abs(ratio - threshold) < 0.01
                
                if matched:
                    results.append({
                        'object_a': det_a,
                        'object_b': det_b,
                        'ratio': ratio,
                        'threshold': threshold,
                        'matched': True,
                        'function': 'height_ratio'
                    })
        
        return results
    
    @staticmethod
    def width_ratio(detections_a: List[Dict], detections_b: List[Dict], 
                    config: Dict) -> List[Dict]:
        threshold = config.get('threshold', 0.5)
        operator = config.get('operator', 'greater_than')
        
        results = []
        for det_a in detections_a:
            for det_b in detections_b:
                width_a = get_box_width(det_a['box'])
                width_b = get_box_width(det_b['box'])
                
                if width_b == 0:
                    continue
                
                ratio = width_a / width_b
                
                matched = False
                if operator == 'less_than':
                    matched = ratio < threshold
                elif operator == 'greater_than':
                    matched = ratio > threshold
                elif operator == 'equal':
                    matched = abs(ratio - threshold) < 0.01
                
                if matched:
                    results.append({
                        'object_a': det_a,
                        'object_b': det_b,
                        'ratio': ratio,
                        'threshold': threshold,
                        'matched': True,
                        'function': 'width_ratio'
                    })
        
        return results
    
    @staticmethod
    def iou_check(detections_a: List[Dict], detections_b: List[Dict], 
                  config: Dict) -> List[Dict]:
        threshold = config.get('threshold', 0.5)
        operator = config.get('operator', 'greater_than')
        
        results = []
        for det_a in detections_a:
            for det_b in detections_b:
                iou = calculate_iou(det_a['box'], det_b['box'])
                
                matched = False
                if operator == 'less_than':
                    matched = iou < threshold
                elif operator == 'greater_than':
                    matched = iou > threshold
                elif operator == 'equal':
                    matched = abs(iou - threshold) < 0.01
                
                if matched:
                    results.append({
                        'object_a': det_a,
                        'object_b': det_b,
                        'iou': iou,
                        'threshold': threshold,
                        'matched': True,
                        'function': 'iou_check'
                    })
        
        return results
    
    @staticmethod
    def distance_check(detections_a: List[Dict], detections_b: List[Dict], 
                       config: Dict) -> List[Dict]:
        threshold = config.get('threshold', 100.0)
        operator = config.get('operator', 'less_than')
        
        results = []
        for det_a in detections_a:
            for det_b in detections_b:
                distance = calculate_distance(det_a['box'], det_b['box'])
                
                matched = False
                if operator == 'less_than':
                    matched = distance < threshold
                elif operator == 'greater_than':
                    matched = distance > threshold
                elif operator == 'equal':
                    matched = abs(distance - threshold) < 1.0
                
                if matched:
                    results.append({
                        'object_a': det_a,
                        'object_b': det_b,
                        'distance': distance,
                        'threshold': threshold,
                        'matched': True,
                        'function': 'distance_check'
                    })
        
        return results


    @staticmethod
    def height_ratio_frame(detections_a: List[Dict], detections_b: List[Dict],
                           config: Dict) -> List[Dict]:
        """
        检测框高度占图片高度的比例

        Args:
            detections_a: 检测结果列表
            detections_b: 未使用（保持接口一致性）
            config: 配置字典，需包含：
                - threshold: 阈值（0-1之间的比例）
                - operator: 运算符 ('less_than', 'greater_than', 'equal')
                - frame_height: 图片高度（像素）

        Returns:
            匹配的检测结果列表
        """
        threshold = config.get('threshold', 0.3)
        operator = config.get('operator', 'greater_than')
        frame_height = config.get('frame_height', 1080)

        results = []
        for det_a in detections_a:
            height_a = get_box_height(det_a['box'])
            ratio = height_a / frame_height if frame_height > 0 else 0

            matched = False
            if operator == 'less_than':
                matched = ratio < threshold
            elif operator == 'greater_than':
                matched = ratio > threshold
            elif operator == 'equal':
                matched = abs(ratio - threshold) < 0.01

            if matched:
                results.append({
                    'object_a': det_a,
                    'ratio': ratio,
                    'threshold': threshold,
                    'matched': True,
                    'function': 'height_ratio_frame',
                    'alert_message': f"检测框高度占图片 {ratio*100:.1f}%"
                })

        return results

    @staticmethod
    def width_ratio_frame(detections_a: List[Dict], detections_b: List[Dict],
                          config: Dict) -> List[Dict]:
        """
        检测框宽度占图片宽度的比例

        Args:
            detections_a: 检测结果列表
            detections_b: 未使用（保持接口一致性）
            config: 配置字典，需包含：
                - threshold: 阈值（0-1之间的比例）
                - operator: 运算符 ('less_than', 'greater_than', 'equal')
                - frame_width: 图片宽度（像素）

        Returns:
            匹配的检测结果列表
        """
        threshold = config.get('threshold', 0.3)
        operator = config.get('operator', 'greater_than')
        frame_width = config.get('frame_width', 1920)

        results = []
        for det_a in detections_a:
            width_a = get_box_width(det_a['box'])
            ratio = width_a / frame_width if frame_width > 0 else 0

            matched = False
            if operator == 'less_than':
                matched = ratio < threshold
            elif operator == 'greater_than':
                matched = ratio > threshold
            elif operator == 'equal':
                matched = abs(ratio - threshold) < 0.01

            if matched:
                results.append({
                    'object_a': det_a,
                    'ratio': ratio,
                    'threshold': threshold,
                    'matched': True,
                    'function': 'width_ratio_frame',
                    'alert_message': f"检测框宽度占图片 {ratio*100:.1f}%"
                })

        return results

    @staticmethod
    def area_ratio_frame(detections_a: List[Dict], detections_b: List[Dict],
                         config: Dict) -> List[Dict]:
        """
        检测框面积占图片面积的比例

        Args:
            detections_a: 检测结果列表
            detections_b: 未使用（保持接口一致性）
            config: 配置字典，需包含：
                - threshold: 阈值（0-1之间的比例）
                - operator: 运算符 ('less_than', 'greater_than', 'equal')
                - frame_width: 图片宽度（像素）
                - frame_height: 图片高度（像素）

        Returns:
            匹配的检测结果列表
        """
        threshold = config.get('threshold', 0.1)
        operator = config.get('operator', 'greater_than')
        frame_width = config.get('frame_width', 1920)
        frame_height = config.get('frame_height', 1080)

        frame_area = frame_width * frame_height
        results = []

        for det_a in detections_a:
            area_a = get_box_area(det_a['box'])
            ratio = area_a / frame_area if frame_area > 0 else 0

            matched = False
            if operator == 'less_than':
                matched = ratio < threshold
            elif operator == 'greater_than':
                matched = ratio > threshold
            elif operator == 'equal':
                matched = abs(ratio - threshold) < 0.01

            if matched:
                results.append({
                    'object_a': det_a,
                    'ratio': ratio,
                    'threshold': threshold,
                    'matched': True,
                    'function': 'area_ratio_frame',
                    'alert_message': f"检测框面积占图片 {ratio*100:.1f}%"
                })

        return results

    @staticmethod
    def size_absolute(detections_a: List[Dict], detections_b: List[Dict],
                      config: Dict) -> List[Dict]:
        """
        检测框绝对尺寸检测（高度、宽度或面积的绝对像素值）

        Args:
            detections_a: 检测结果列表
            detections_b: 未使用（保持接口一致性）
            config: 配置字典，需包含：
                - dimension: 维度类型 ('height', 'width', 'area')
                - threshold: 阈值（像素值）
                - operator: 运算符 ('less_than', 'greater_than', 'equal')

        Returns:
            匹配的检测结果列表
        """
        dimension = config.get('dimension', 'height')
        threshold = config.get('threshold', 200.0)
        operator = config.get('operator', 'greater_than')

        results = []
        for det_a in detections_a:
            box = det_a['box']

            if dimension == 'height':
                value = get_box_height(box)
                unit = '像素高'
            elif dimension == 'width':
                value = get_box_width(box)
                unit = '像素宽'
            elif dimension == 'area':
                value = get_box_area(box)
                unit = '平方像素'
            else:
                continue

            matched = False
            if operator == 'less_than':
                matched = value < threshold
            elif operator == 'greater_than':
                matched = value > threshold
            elif operator == 'equal':
                matched = abs(value - threshold) < 1.0

            if matched:
                results.append({
                    'object_a': det_a,
                    'value': value,
                    'threshold': threshold,
                    'matched': True,
                    'function': 'size_absolute',
                    'dimension': dimension,
                    'alert_message': f"检测框{dimension}为 {value:.0f}{unit}"
                })

        return results


BUILTIN_FUNCTIONS = {
    'area_ratio': BuiltinFunction.area_ratio,
    'height_ratio': BuiltinFunction.height_ratio,
    'width_ratio': BuiltinFunction.width_ratio,
    'iou_check': BuiltinFunction.iou_check,
    'distance_check': BuiltinFunction.distance_check,
    'height_ratio_frame': BuiltinFunction.height_ratio_frame,
    'width_ratio_frame': BuiltinFunction.width_ratio_frame,
    'area_ratio_frame': BuiltinFunction.area_ratio_frame,
    'size_absolute': BuiltinFunction.size_absolute,
}

