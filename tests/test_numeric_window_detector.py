from app.core.numeric_window_detector import NumericWindowDetector


def _evaluate(detector, sample_id, value, **overrides):
    config = {
        'window_size': 3,
        'direction': 'both',
        'relative_threshold': 0.5,
        'absolute_threshold': 2,
        'confirmation_count': 1,
    }
    config.update(overrides)
    return detector.evaluate('condition-1', sample_id, value, **config)


def test_uses_historical_median_and_does_not_include_current_sample_in_baseline():
    detector = NumericWindowDetector()

    assert not _evaluate(detector, 1, 4)['warmed_up']
    assert not _evaluate(detector, 2, 5)['warmed_up']
    assert not _evaluate(detector, 3, 4)['warmed_up']

    result = _evaluate(detector, 4, 8)

    assert result['baseline'] == 4
    assert result['delta'] == 4
    assert result['relative_change'] == 1
    assert result['triggered'] is True


def test_duplicate_sample_is_not_added_or_triggered_twice():
    detector = NumericWindowDetector()
    for sample_id in range(1, 4):
        _evaluate(detector, sample_id, 2)

    first = _evaluate(detector, 4, 6)
    duplicate = _evaluate(detector, 4, 99)

    assert first['triggered'] is True
    assert duplicate['sampled'] is False
    assert duplicate['duplicate_sample'] is True
    assert duplicate['triggered'] is True
    assert duplicate['emitted'] is False
    assert duplicate['current_count'] == 6


def test_confirmation_edge_trigger_and_rearm_after_recovery():
    detector = NumericWindowDetector()
    config = {'direction': 'increase', 'confirmation_count': 2}
    for sample_id in range(1, 4):
        _evaluate(detector, sample_id, 10, **config)

    assert _evaluate(detector, 4, 20, **config)['triggered'] is False
    assert _evaluate(detector, 5, 20, **config)['triggered'] is True
    assert _evaluate(detector, 6, 20, **config)['triggered'] is False

    assert _evaluate(detector, 7, 10, **config)['armed'] is False
    assert _evaluate(detector, 8, 10, **config)['armed'] is True
    assert _evaluate(detector, 9, 20, **config)['triggered'] is False
    assert _evaluate(detector, 10, 20, **config)['triggered'] is True


def test_zero_baseline_uses_one_as_relative_denominator():
    detector = NumericWindowDetector()
    for sample_id in range(1, 4):
        _evaluate(detector, sample_id, 0, absolute_threshold=3)

    result = _evaluate(detector, 4, 3, absolute_threshold=3)

    assert result['baseline'] == 0
    assert result['relative_change'] == 3
    assert result['triggered'] is True


def test_pending_anomalies_do_not_shift_baseline_before_confirmation():
    detector = NumericWindowDetector()
    config = {'confirmation_count': 3}
    for sample_id in range(1, 4):
        _evaluate(detector, sample_id, 10, **config)

    first = _evaluate(detector, 4, 20, **config)
    second = _evaluate(detector, 5, 20, **config)
    third = _evaluate(detector, 6, 20, **config)

    assert [first['baseline'], second['baseline'], third['baseline']] == [10, 10, 10]
    assert first['confirmation_progress'] == 1
    assert second['confirmation_progress'] == 2
    assert third['triggered'] is True
