import os


def save_frame(frame_data, save_path: str):
    import cv2
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    cv2.imwrite(save_path, frame_data)