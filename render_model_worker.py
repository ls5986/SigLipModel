"""Persistent Render worker for SigLIP rooms and Model Workbench V1 jobs."""
from __future__ import annotations

import json
import os
import signal
import threading
from datetime import UTC, datetime

import config  # Load repository-local defaults before validating configuration.
from cloud_autolabel import heartbeat, process
from cloud_runtime import from_env
from studio_data import now


def workbench_heartbeat(store,status,detail=None):
    current=store.document("workbench-worker") or {}
    return store.save_document("workbench-worker",{
        "status":status,"at":now(),"detail":detail,
        "policy":"render-workbench-worker-v1",
    },current.get("revision",0))


def heartbeat_online(document,threshold_seconds=90):
    try:
        at=datetime.fromisoformat(document["at"])
        if at.tzinfo is None:
            at=at.replace(tzinfo=UTC)
        age=(datetime.now(UTC)-at).total_seconds()
    except (KeyError,TypeError,ValueError):
        return False
    return age<threshold_seconds and document.get("status") in {
        "ready","running","loading",
    }


def refuse_parallel_worker(store):
    current=store.document("workbench-worker") or {}
    if heartbeat_online(current):
        raise RuntimeError(
            "Another model worker is online. Stop the local worker and wait "
            "90 seconds before starting the Render worker."
        )


def fail_interrupted_training(store):
    """Release a job left running by an earlier, now-offline worker."""
    from model_workbench import pending_training

    request=pending_training(store)
    if not request or request.get("status")!="running":
        return False
    key="workbench-training:"+request["id"]
    current=store.document(key)
    failure={
        **{k:v for k,v in current.items() if k!="revision"},
        "status":"failed","completed_at":now(),
        "error":"Model worker restarted during training. Queue a fresh run; the frozen dataset is unchanged.",
    }
    saved=store.save_document(key,failure,current["revision"])
    latest=store.document("workbench-training-latest") or {}
    if latest.get("id")==request["id"]:
        store.save_document(
            "workbench-training-latest",
            {k:v for k,v in saved.items() if k!="revision"},
            latest.get("revision",0),
        )
    return True


def model_detail(scorer):
    if scorer.pointer:
        return {
            "mode":"scoring_and_training",
            "model_version":scorer.pointer["version"],
            "components":scorer.pointer.get("components",{}),
            "instance":os.environ.get("RENDER_INSTANCE_ID","render"),
        }
    return {
        "mode":"training_only_until_first_v1_candidate",
        "model_error":scorer.load_error,
        "instance":os.environ.get("RENDER_INSTANCE_ID","render"),
    }


def main():
    os.environ.setdefault("STUDIO_AUTOLABEL_PROVIDER","hybrid")
    stop=threading.Event()
    for name in ("SIGTERM","SIGINT"):
        if hasattr(signal,name):
            signal.signal(getattr(signal,name),lambda *_:stop.set())

    store=from_env().get_studio().store
    refuse_parallel_worker(store)
    fail_interrupted_training(store)
    heartbeat(store,"loading","Loading frozen SigLIP checkpoint",stage="rooms")
    workbench_heartbeat(store,"loading",{
        "instance":os.environ.get("RENDER_INSTANCE_ID","render"),
    })
    try:
        from automatic_labels import SiglipLabels, active_policy
        from workbench_training import process_training_request
        from workbench_worker import WorkbenchScorer, process_pending_runs

        rooms=SiglipLabels()
        rooms.policy=active_policy()
        rooms.stage="rooms"
        scorer=WorkbenchScorer(store,rooms,allow_untrained=True)
        print(json.dumps({"status":"ready",**model_detail(scorer)}),flush=True)
        while not stop.is_set():
            heartbeat(store,"ready",stage="rooms")
            workbench_heartbeat(store,"ready",model_detail(scorer))
            for identifier in store.autolabel_pending(stage="rooms"):
                if stop.is_set():
                    break
                try:
                    if process(store,identifier,rooms):
                        print("Completed rooms: "+identifier,flush=True)
                except Exception:
                    print(
                        "Room request failed; inspect its saved status before retrying.",
                        flush=True,
                    )
            if scorer.pointer:
                processed=process_pending_runs(store,scorer)
                if processed:
                    print(f"Completed {processed} workbench model run(s).",flush=True)
            if not stop.is_set() and process_training_request(store,scorer):
                print("Processed a workbench training request.",flush=True)
                try:
                    scorer.load_current()
                except (KeyError,OSError,ValueError) as error:
                    scorer.load_error=str(error)[:400]
            stop.wait(5)
    finally:
        try:
            workbench_heartbeat(store,"stopped",{
                "instance":os.environ.get("RENDER_INSTANCE_ID","render"),
            })
            heartbeat(store,"stopped",stage="rooms")
        except Exception:
            pass


if __name__=="__main__":
    try:
        main()
    except (ImportError,RuntimeError,ValueError) as error:
        raise SystemExit(str(error)) from None
