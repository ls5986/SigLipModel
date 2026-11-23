from paired_condition import run_idle_loop


def test_idle_backoff_and_heartbeat():
    now = [0]
    waits, beats, polls = [], [], []
    class Stop:
        def is_set(self): return len(waits) >= 7
        def wait(self, seconds):
            waits.append(seconds)
            now[0] += seconds
    run_idle_loop(None, Stop(), clock=lambda: now[0],
                  heartbeat=lambda _: beats.append(now[0]),
                  train=lambda _: polls.append(now[0]) or False, predict=lambda _: False)
    assert waits == [6, 12, 24, 30, 30, 30, 30]
    assert beats == [0, 72, 132]
    assert len(polls) == 7


def test_success_resets_delay_and_forces_next_heartbeat():
    now = [0]
    waits, beats = [], []
    outcomes = iter([False, False, True, False, False])
    class Stop:
        def is_set(self): return len(waits) >= 5
        def wait(self, seconds):
            waits.append(seconds)
            now[0] += seconds
    run_idle_loop(None, Stop(), clock=lambda: now[0],
                  heartbeat=lambda _: beats.append(now[0]),
                  train=lambda _: next(outcomes), predict=lambda _: False)
    assert waits == [6, 12, 3, 6, 12]
    assert beats == [0, 21]
