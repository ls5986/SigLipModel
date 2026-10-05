from types import SimpleNamespace
from hosted_label_worker import STOP, eligible_request, poll_once


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
