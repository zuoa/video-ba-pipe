from types import SimpleNamespace

from app.web import webapp as webapp_module


class _Field:
    def __eq__(self, _other):
        return self

    def __and__(self, _other):
        return self

    def in_(self, _values):
        return self


class _Query(list):
    def where(self, _condition):
        return self


def test_rotation_api_reports_licensed_runtime_and_revisit_bounds(monkeypatch):
    workflows = _Query([
        SimpleNamespace(data_dict={'nodes': [{'type': 'source', 'dataId': 1}]}),
        SimpleNamespace(data_dict={'nodes': [{'type': 'source', 'dataId': 2}]}),
        SimpleNamespace(data_dict={'nodes': [{'type': 'source', 'dataId': 3}]}),
    ])

    class Workflow:
        is_active = _Field()
        is_template = _Field()

        @classmethod
        def select(cls):
            return workflows

    class VideoSource:
        id = _Field()
        enabled = _Field()

        @classmethod
        def select(cls):
            return _Query([
                SimpleNamespace(id=1),
                SimpleNamespace(id=2),
                SimpleNamespace(id=3),
            ])

    monkeypatch.setattr(webapp_module, 'Workflow', Workflow)
    monkeypatch.setattr(webapp_module, 'VideoSource', VideoSource)
    monkeypatch.setattr(webapp_module, 'get_source_rotation_config', lambda: {
        'enabled': True, 'batch_size': 2, 'dwell_seconds': 30,
    })
    monkeypatch.setattr(
        webapp_module,
        'runtime_entitlements',
        lambda: {'source_ids': {1, 2}},
    )
    monkeypatch.setattr(webapp_module, 'get_inference_resource_status', lambda: {
        'worker_online': True,
        'source_rotation': {
            'enabled': True,
            'effective_concurrency': 2,
            'startup_p95_seconds': 4,
            'drain_p95_seconds': 1,
        },
    })
    monkeypatch.setattr(
        webapp_module,
        'get_recording_storage_config',
        lambda: SimpleNamespace(post_alert_seconds=15),
    )

    endpoint = webapp_module.get_system_source_rotation_config
    while hasattr(endpoint, '__wrapped__'):
        endpoint = endpoint.__wrapped__
    with webapp_module.app.app_context():
        response = endpoint()
        payload = response.get_json()

    assert payload['configured_candidate_count'] == 3
    assert payload['eligible_source_count'] == 2
    assert payload['estimated_revisit_seconds'] == {
        'best': 30,
        'p95': 35,
        'worst': 120,
    }
    assert payload['runtime_status']['effective_concurrency'] == 2
