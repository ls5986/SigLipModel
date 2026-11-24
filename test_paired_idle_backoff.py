import pytest
from paired_condition import run_idle_loop


@pytest.mark.parametrize("prior_age", range(60))
def test_persisted_heartbeat_stays_fresh_with_database_throttle(prior_age):
    now = [0]
    persisted = [-prior_age]
    ages, attempts, polls = [], [], []

    class Stop:
        def is_set(self):
            return now[0] >= 360

        def wait(self, seconds):
            # Check the worst age at the end of each sleep, before the next write.
            now[0] += seconds
            ages.append(now[0] - persisted[0])

    def throttled_heartbeat(_):
        attempts.append(now[0])
        if now[0] - persisted[0] >= 60:
            persisted[0] = now[0]

    run_idle_loop(None, Stop(), clock=lambda: now[0],
                  heartbeat=throttled_heartbeat,
                  train=lambda _: polls.append(now[0]) or False,
                  predict=lambda _: False)
    assert max(ages) <= 70
    assert polls[:5] == [0, 6, 18, 42, 72]
    assert len(polls) == 14  # 14 polls in six minutes, not every heartbeat
    assert len(attempts) == 36


def test_work_resets_poll_delay_and_restores_ready():
    now, polls, beats = [0], [], []

    class Stop:
        def is_set(self):
            return now[0] >= 30

        def wait(self, seconds):
            now[0] += seconds

    def train(_):
        polls.append(now[0])
        return now[0] == 18

    run_idle_loop(None, Stop(), clock=lambda: now[0],
                  heartbeat=lambda _: beats.append(now[0]),
                  train=train, predict=lambda _: False)
    assert polls == [0, 6, 18, 21, 27]
    assert beats == [0, 10, 18, 28]


def test_heartbeat_failure_is_not_silenced():
    class Stop:
        def is_set(self): return False
    def failed(_):
        raise RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        run_idle_loop(None, Stop(), clock=lambda: 0, heartbeat=failed,
                      train=lambda _: False, predict=lambda _: False)


@pytest.mark.parametrize("status,age,should_recover", [
    ("ready", 5, True), ("ready", 81, False),
    ("running", 5, False), ("ready", -5, False),
])
def test_heartbeat_conflict_only_recovers_a_fresh_ready_state(monkeypatch, status, age, should_recover):
    import sys
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from paired_condition import save_idle_heartbeat
    class Conflict(RuntimeError): pass
    monkeypatch.setitem(sys.modules, 'cloud_store', SimpleNamespace(StateRevisionConflict=Conflict))
    class Store:
        reads = 0
        def document(self, key):
            self.reads += 1
            if self.reads == 1:
                return {'revision': 1}
            return {'revision': 2, 'status': status,
                    'at': (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()}
        def save_document(self, key, payload, revision):
            raise Conflict('stale')
    store = Store()
    if should_recover:
        save_idle_heartbeat(store)
        assert store.reads == 2
    else:
        with pytest.raises(Conflict):
            save_idle_heartbeat(store)


def test_heartbeat_does_not_mask_other_database_errors(monkeypatch):
    import sys
    from types import SimpleNamespace
    from paired_condition import save_idle_heartbeat
    class Conflict(RuntimeError): pass
    monkeypatch.setitem(sys.modules, 'cloud_store', SimpleNamespace(StateRevisionConflict=Conflict))
    class Store:
        def document(self, key): return {'revision': 1}
        def save_document(self, key, payload, revision): raise OSError('database unavailable')
    with pytest.raises(OSError, match='database unavailable'):
        save_idle_heartbeat(Store())

