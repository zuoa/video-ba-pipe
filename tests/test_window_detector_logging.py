import app.core.window_detector as window_detector_module
from app.core.window_detector import WindowDetector


def test_window_detector_logs_config_only_when_it_changes(monkeypatch):
    messages = []
    monkeypatch.setattr(
        window_detector_module.logger,
        "info",
        lambda message: messages.append(message),
    )
    detector = WindowDetector()
    trigger = {"enable": False}
    suppression = {"enable": True, "seconds": 30}

    detector.load_trigger_condition(1, "alert", trigger)
    detector.load_suppression(1, "alert", suppression)
    detector.load_trigger_condition(1, "alert", trigger)
    detector.load_suppression(1, "alert", suppression)

    assert len(messages) == 2


def test_window_detector_rate_limits_suppression_logs(monkeypatch):
    messages = []
    monkeypatch.setattr(
        window_detector_module.logger,
        "info",
        lambda message: messages.append(message),
    )
    detector = WindowDetector()
    detector.load_suppression(1, "alert", {"enable": True, "seconds": 30})
    detector.record_trigger(1, "alert", 100.0)
    messages.clear()

    assert detector.check_suppression(1, "alert", 101.0)[0] is False
    assert detector.check_suppression(1, "alert", 102.0)[0] is False
    assert detector.check_suppression(1, "alert", 110.9)[0] is False
    assert detector.check_suppression(1, "alert", 111.0)[0] is False

    assert len(messages) == 2
