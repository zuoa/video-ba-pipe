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


BUILTIN_FUNCTIONS = {
    'area_ratio': BuiltinFunction.area_ratio,
    'height_ratio': BuiltinFunction.height_ratio,
    'width_ratio': BuiltinFunction.width_ratio,
    'iou_check': BuiltinFunction.iou_check,
    'distance_check': BuiltinFunction.distance_check,
}

