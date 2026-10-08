"""Hosted CPU worker with isolated draft-label and ActVision v2 model lanes.

The v2 lane can train research candidates and serve approved inference artifacts,
but it cannot promote releases.
"""
from __future__ import annotations

import os
import signal
import threading
import time

import config
from cloud_autolabel import heartbeat, process

STOP = threading.Event()


def eligible_request(request, stage, provider="copilot"):
    if not request or request.get("stage", "rooms") != stage:
        return False
    # Never drain old bulk requests merely because a new worker was deployed.
    return request.get("mode") != "all" and (
        stage == "rooms" or (
            request.get("mode") == "test"
            and request.get("label_provider", "openai") == provider
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
            if not eligible_request(store.document("autolabel-request:" + identifier), stage,
                                    getattr(classifier, "provider", "copilot")):
                continue
            try:
                count += bool(process_request(store, identifier, classifier))
            except Exception:
                print("Draft request failed; inspect saved status before retrying.", flush=True)
    return count


def optional_copilot(store, factory=None):
    """Keep room tagging available while an optional provider is unconfigured."""
    limit = int(os.environ.get("STUDIO_COPILOT_MAX_CALLS_PER_DAY", "2"))
    if not 1 <= limit <= 10:
        raise ValueError("Hosted Copilot daily budget must be 1 through 10")
    os.environ["STUDIO_COPILOT_MAX_CALLS_PER_DAY"] = str(limit)
    if factory is None:
        from copilot_labels import CopilotLabels
        factory = CopilotLabels
    classifier = factory(store)
    try:
        classifier.transport.preflight()
    except Exception:
        # Never persist provider exception text, tokens or authentication bodies.
        heartbeat(store, "unconfigured", "Copilot authentication/model preflight failed; no feature requests processed.",
                  stage="features", provider="copilot")
        print("Copilot preflight failed. Room tagging remains available; Copilot feature requests stay blocked.", flush=True)
        try:
            classifier.close()
        except Exception:
            pass
        return None
    return classifier


def optional_openai(store, factory=None):
    limit = int(os.environ.get("STUDIO_OPENAI_MAX_CALLS_PER_DAY", "2"))
    if limit < 0:
        raise ValueError("Hosted OpenAI daily budget must be nonnegative; zero disables the cap")
    os.environ["STUDIO_OPENAI_MAX_CALLS_PER_DAY"] = str(limit)
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        heartbeat(store, "unconfigured", "Add OPENAI_API_KEY to this worker's managed environment; no feature requests processed.",
                  stage="features", provider="openai")
        print("OpenAI key missing. Room tagging remains available; OpenAI feature requests stay blocked.", flush=True)
        return None
    from openai_labels import OpenAILabels, hybrid_policy
    classifier = (factory or OpenAILabels)(store)
    classifier.policy = hybrid_policy()
    classifier.stage = "features"
    classifier.provider = "openai"
    classifier.paid = True
    return classifier


def main():
    if os.environ.get("STUDIO_PAIRED_AUTO_BATCH", ""):
        from paired_auto_score import run
        return run()
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
    from provision_semantic_encoder import verify
    checkpoint = verify()
    print("Semantic text checkpoint verified: " + checkpoint["revision"] + "; no training or scoring enabled.", flush=True)
    os.environ["STUDIO_AUTOLABEL_PROVIDER"] = "hybrid"
    from cloud_runtime import from_env
    from automatic_labels import SiglipLabels, active_policy
    store = from_env().get_studio().store
    classifiers = []
    paid = None
    try:
        heartbeat(store, "loading", stage="rooms")
        rooms = SiglipLabels()
        rooms.policy = active_policy()
        rooms.stage = "rooms"
        classifiers.append(rooms)
        if os.environ.get("STUDIO_OPENAI_ENABLED", "false").lower() == "true":
            paid = optional_openai(store)
        elif os.environ.get("STUDIO_COPILOT_ENABLED", "false").lower() == "true":
            paid = optional_copilot(store)
        if paid:
            classifiers.append(paid)
        last_heartbeat = 0
        last_experiment = 0
        last_v2_model = 0
        last_event_recovery = 0
        if os.environ.get("STUDIO_V2_MODEL_ENABLED", "false").lower() == "true":
            from v2_training import heartbeat as v2_heartbeat
            v2_heartbeat(store, "ready", detail={"lane": "model"})
            from v2_auto_bootstrap import maybe_enqueue
            try:
                bootstrap = maybe_enqueue(store)
                if bootstrap.get("status") != "disabled":
                    print("ActVision v2 auto-bootstrap: " + str(bootstrap.get("status")), flush=True)
            except Exception:
                print("ActVision v2 auto-bootstrap could not queue; inspect saved marker.", flush=True)
        while not STOP.is_set():
            if time.monotonic() - last_heartbeat >= 30:
                for classifier in classifiers:
                    heartbeat(store, "ready", stage=classifier.stage,
                              provider=getattr(classifier, "provider", None))
                from typed_label_assistant import heartbeat as typed_heartbeat
                typed_heartbeat(store, paid if getattr(paid, "provider", None) == "openai" else None)
                last_heartbeat = time.monotonic()
            poll_once(store, classifiers)
            if paid and getattr(paid, "provider", None) == "openai":
                from typed_label_assistant import poll
                try:
                    from typed_draft_batch import advance, budget_available
                    if budget_available(store, paid):
                        advance(store)
                        poll(store, paid)
                except Exception:
                    print("Typed draft polling failed; inspect request status before retrying.", flush=True)
            if time.monotonic() - last_experiment >= 60:
                from experimental_candidate import poll_training, poll_prediction
                try:
                    poll_training(store)
                except Exception:
                    print("Experimental candidate failed; inspect saved non-secret status.", flush=True)
                last_experiment = time.monotonic()
            # Predictions should not wait for the 60-second training scheduler.
            try:
                from experimental_candidate import poll_prediction
                poll_prediction(store)
            except Exception:
                print("Experimental prediction failed; inspect saved status.", flush=True)

            if (
                os.environ.get("STUDIO_EVENT_RECOVERY_ENABLED", "false").lower() == "true"
                and time.monotonic() - last_event_recovery >= 10
            ):
                from event_media_recovery import poll as poll_event_recovery
                try:
                    poll_event_recovery(store)
                except Exception:
                    print("Acquisition event photo recovery failed; inspect recovery heartbeat.", flush=True)
                last_event_recovery = time.monotonic()

            if (
                os.environ.get("STUDIO_V2_MODEL_ENABLED", "false").lower() == "true"
                and time.monotonic() - last_v2_model >= 5
            ):
                from v2_training import poll_training, heartbeat as v2_heartbeat
                active_heartbeat = threading.Event()
                def keep_model_heartbeat():
                    while not active_heartbeat.wait(25):
                        try:
                            v2_heartbeat(store, "running", detail={"lane": "model", "operation": "training"})
                        except Exception:
                            pass
                helper = None
                try:
                    request = store.document("actvision-v2-training-current") or {}
                    if request.get("status") == "queued":
                        helper = threading.Thread(target=keep_model_heartbeat, daemon=True)
                        helper.start()
                    poll_training(store, rooms)
                    from v2_auto_bootstrap import reconcile as reconcile_v2_bootstrap
                    reconcile_v2_bootstrap(store)
                    from v2_inference import poll as poll_v2_inference
                    from v2_property_prediction import poll as poll_v2_property
                    inference_ran = poll_v2_inference(store, rooms)
                    property_ran = poll_v2_property(store, rooms)
                    if request.get("status") not in {"queued", "running"}:
                        v2_heartbeat(store, "ready", detail={
                            "lane": "model",
                            "latest_operation": "inference" if inference_ran else "property_prediction" if property_ran else "idle",
                        })
                except Exception:
                    print("ActVision v2 training failed; inspect saved run states.", flush=True)
                finally:
                    active_heartbeat.set()
                    if helper:
                        helper.join(timeout=2)
                    last_v2_model = time.monotonic()
            STOP.wait(5)
    finally:
        if paid:
            paid.close()
        for classifier in classifiers:
            heartbeat(store, "stopped", stage=classifier.stage,
                      provider=getattr(classifier, "provider", None))
        if os.environ.get("STUDIO_V2_MODEL_ENABLED", "false").lower() == "true":
            try:
                from v2_training import heartbeat as v2_heartbeat
                v2_heartbeat(store, "stopped", detail={"lane": "model"})
            except Exception:
                pass


if __name__ == "__main__":
    main()
