from types import SimpleNamespace
from hosted_label_worker import STOP, eligible_request, poll_once, optional_copilot, optional_openai


def test_openai_key_gate_and_bounded_hybrid_provider(monkeypatch):
    import pytest
    saved = []
    monkeypatch.setattr("hosted_label_worker.heartbeat", lambda *args, **kwargs: saved.append((args, kwargs)))
    monkeypatch.setenv("STUDIO_OPENAI_MAX_CALLS_PER_DAY", "2")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert optional_openai(object(), lambda store: (_ for _ in ()).throw(AssertionError())) is None
    assert saved[0][0][1] == "unconfigured"
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-placeholder")
    classifier = optional_openai(object(), lambda store: SimpleNamespace())
    assert classifier.stage == "features" and classifier.provider == "openai" and classifier.paid
    assert eligible_request({"stage":"features","mode":"test","label_provider":"openai"}, "features", classifier.provider)
    assert not eligible_request({"stage":"features","mode":"all","label_provider":"openai"}, "features", classifier.provider)
    assert not eligible_request({"stage":"features","mode":"test","label_provider":"copilot"}, "features", classifier.provider)
    monkeypatch.setenv("STUDIO_OPENAI_MAX_CALLS_PER_DAY", "11")
    with pytest.raises(ValueError, match="daily budget"):
        optional_openai(object())


def test_unconfigured_copilot_never_claims_features_or_persists_provider_secrets(monkeypatch, capsys):
    monkeypatch.setenv("STUDIO_COPILOT_MAX_CALLS_PER_DAY", "2")
    saved = []
    closed = []
    monkeypatch.setattr("hosted_label_worker.heartbeat", lambda *args, **kwargs: saved.append((args, kwargs)))
    def failed_preflight():
        raise ValueError("authentication provider-secret-body")
    classifier = SimpleNamespace(transport=SimpleNamespace(preflight=failed_preflight), close=lambda: closed.append(True))
    assert optional_copilot(object(), lambda store: classifier) is None
    assert closed == [True]
    assert saved[0][0][1] == "unconfigured"
    assert saved[0][1] == {"stage": "features", "provider": "copilot"}
    assert "provider-secret-body" not in str(saved) + capsys.readouterr().out


def test_successful_copilot_preflight_returns_provider_and_enforces_budget(monkeypatch):
    import pytest
    calls = []
    classifier = SimpleNamespace(transport=SimpleNamespace(preflight=lambda: calls.append("checked")))
    monkeypatch.setenv("STUDIO_COPILOT_MAX_CALLS_PER_DAY", "2")
    assert optional_copilot(object(), lambda store: classifier) is classifier
    assert calls == ["checked"]
    monkeypatch.setenv("STUDIO_COPILOT_MAX_CALLS_PER_DAY", "11")
    with pytest.raises(ValueError, match="daily budget"):
        optional_copilot(object(), lambda store: (_ for _ in ()).throw(AssertionError()))


def test_bulk_and_other_provider_requests_are_not_processed():
    assert not eligible_request({"stage":"rooms","mode":"all"}, "rooms")
    assert not eligible_request({"stage":"features","mode":"all","label_provider":"copilot"}, "features")
    assert not eligible_request({"stage":"features","mode":"test","label_provider":"openai"}, "features")
    assert eligible_request({"stage":"rooms"}, "rooms")
    assert eligible_request({"stage":"features","mode":"test","label_provider":"copilot"}, "features")


def test_poll_only_claims_small_explicit_copilot_requests():
    STOP.clear()
    requests = {
        "bulk": {"stage":"features","mode":"all","label_provider":"copilot"},
        "test": {"stage":"features","mode":"test","label_provider":"copilot"},
        "other": {"stage":"features","mode":"test","label_provider":"openai"},
    }
    class Store:
        def autolabel_pending(self, stage): return list(requests)
        def document(self, key): return requests[key.split(":")[1]]
    calls = []
    assert poll_once(Store(), [SimpleNamespace(stage="features")],
                     lambda store, identifier, classifier: calls.append(identifier) or True) == 1
    assert calls == ["test"]
    STOP.set()
    assert poll_once(Store(), [SimpleNamespace(stage="features")],
                     lambda *args: (_ for _ in ()).throw(AssertionError())) == 0
    STOP.clear()
