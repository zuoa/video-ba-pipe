try:
    import cv2 as _cv2
except ImportError:  # pragma: no cover - optional in lightweight test envs
    _cv2 = None


class _MissingCV2:
    def __getattr__(self, name):
        raise ImportError("OpenCV (cv2) is required for this operation")


cv2 = _cv2 if _cv2 is not None else _MissingCV2()


def require_cv2():
    if _cv2 is None:
        raise ImportError("OpenCV (cv2) is required for this operation")
    return _cv2
