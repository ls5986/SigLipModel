"""Hosted draft-label worker. No scoring, training, bulk enqueue or promotion."""
from __future__ import annotations

import os
import signal
import threading
import time

import config
from cloud_autolabel import heartbeat, process

STOP = threading.Event()


def eligible_request(request, stage):
    if not request or request.get("stage", "rooms") != stage:
        return False
    # Never drain old bulk requests merely because a new worker was deployed.
    return request.get("mode") != "all" and (
        stage == "rooms" or (
            request.get("mode") == "test"
            and request.get("label_provider") == "copilot"
        )
    )


def poll_once(store, classifiers, process_request=process):
    count = 0
    for classifier in classifiers:
        if STOP.is_set():
            break
        stage = classifier.stage
        for identifier in store.autolabel_pending(stage=stage):
            if STOP.is_set():
                break
            if not eligible_request(store.document("autolabel-request:" + identifier), stage):
                continue
            try:
                count += bool(process_request(store, identifier, classifier))
            except Exception:
                print("Draft request failed; inspect saved status before retrying.", flush=True)
    return count


def main():
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    if os.environ.get("STUDIO_LABEL_WORKER_PAUSED", "true").lower() != "false":
        print("Draft-label worker paused. No database access or model calls.", flush=True)
        while not STOP.wait(5):
            pass
        return
    expected = os.environ.get("STUDIO_LABEL_WORKER_WORKSPACE")
    if not expected or expected != os.environ.get("STUDIO_WORKSPACE_ID"):
        raise ValueError("Explicit worker workspace binding required")
    os.environ["STUDIO_AUTOLABEL_PROVIDER"] = "hybrid"
    from cloud_runtime import from_env
    from automatic_labels import SiglipLabels, active_policy
    store = from_env().get_studio().store
    classifiers = []
    copilot = None
    try:
        heartbeat(store, "loading", stage="rooms")
        rooms = SiglipLabels()
        rooms.policy = active_policy()
        rooms.stage = "rooms"
        classifiers.append(rooms)
        if os.environ.get("STUDIO_COPILOT_ENABLED", "false").lower() == "true":
            # Enforced by the existing provider's durable daily budget.
            limit = int(os.environ.get("STUDIO_COPILOT_MAX_CALLS_PER_DAY", "2"))
            if not 1 <= limit <= 10:
                raise ValueError("Hosted Copilot daily budget must be 1 through 10")
            os.environ["STUDIO_COPILOT_MAX_CALLS_PER_DAY"] = str(limit)
            from copilot_labels import CopilotLabels
            copilot = CopilotLabels(store)
            copilot.transport.preflight()
            classifiers.append(copilot)
        last_heartbeat = 0
        while not STOP.is_set():
            if time.monotonic() - last_heartbeat >= 30:
                for classifier in classifiers:
                    heartbeat(store, "ready", stage=classifier.stage,
                              provider=getattr(classifier, "provider", None))
                last_heartbeat = time.monotonic()
            poll_once(store, classifiers)
            STOP.wait(5)
    finally:
        if copilot:
            copilot.close()
        for classifier in classifiers:
            heartbeat(store, "stopped", stage=classifier.stage,
                      provider=getattr(classifier, "provider", None))


if __name__ == "__main__":
    main()
