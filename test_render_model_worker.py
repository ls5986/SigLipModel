from datetime import UTC, datetime, timedelta

import render_model_worker as module


class Store:
    def __init__(self):
        self.docs={}
    def document(self,key):
        value=self.docs.get(key)
        return dict(value) if value else None
    def save_document(self,key,value,expected):
        current=self.docs.get(key) or {}
        assert current.get("revision",0)==expected
        self.docs[key]={**value,"revision":expected+1}
        return dict(self.docs[key])


def test_fresh_online_worker_is_refused():
    store=Store()
    store.docs["workbench-worker"]={
        "status":"ready","at":datetime.now(UTC).isoformat(),"revision":1,
    }
    try:
        module.refuse_parallel_worker(store)
        assert False,"expected the parallel-worker guard"
    except RuntimeError as error:
        assert "Another model worker is online" in str(error)


def test_stale_worker_does_not_block_startup():
    store=Store()
    store.docs["workbench-worker"]={
        "status":"ready",
        "at":(datetime.now(UTC)-timedelta(minutes=3)).isoformat(),
        "revision":1,
    }
    module.refuse_parallel_worker(store)


def test_render_blueprint_has_separate_persistent_worker():
    from pathlib import Path
    import yaml

    blueprint=yaml.safe_load(Path("render.yaml").read_text())
    worker=next(
        service for service in blueprint["services"]
        if service["name"]=="acq-vision-model-worker-dev"
    )
    assert worker["type"]=="worker"
    assert worker["plan"]=="1c-2g"
    assert worker["disk"]["mountPath"]=="/var/data"
    assert worker["startCommand"]=="python -B render_model_worker.py"
