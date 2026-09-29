"""Real browser assessment workflow against temporary labels and a fake model."""
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import review_server
from assessments import Assessments
from launch import create_server
from prompt_lab import PromptLab
from studio_api import Studio
from test_prompt_lab import output_for, response_for
from test_studio_data import imported_store

ROOT = Path(__file__).resolve().parent
review_server.ROOT = ROOT
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    store = imported_store(root)
    lab = PromptLab(root, store.property, store.image_path, store.apply_proposals,
                    lambda: (_ for _ in ()).throw(AssertionError("No real key")))
    calls = []

    def model(request):
        calls.append(request)
        result = output_for(request)
        result.update(target_fit="target", target_reason="Visible work remains")
        for image in result["images"]:
            image.update(context="subject", renovation_scope_score=.71)
        return response_for(result)

    lab._call_model = model
    studio = Studio.__new__(Studio)
    studio.store, studio.prompts = store, lab
    studio.assessments = Assessments(studio, root / "jobs")
    studio.assessments.history = lambda identifier=None: ({
        "timing_verified":False,"blocked":False,"acquisition_status":"prior_acquisition_candidate",
        "source":{"Prior Sale Date":"2026-01-01","Last Sale Date":"2026-03-01"},
        "source_rows":[2],"review":None,"evidence_hash":"fixture","training_gate":"Requires review",
    } if identifier else None)
    studio.summary = lambda: {
        **store.summary(), "token": "isolated", "jobs": [], "prompts": lab.list_prompts(),
        "prompt_runs": [], "candidate": None, "isolated": True,
    }
    app = SimpleNamespace(token="isolated", rows={}, get_studio=lambda: studio)
    studio.app = app
    server = create_server(0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            ["node", str(ROOT / "test_assessment_browser.cjs")],
            env={**os.environ, "TEST_URL": f"http://127.0.0.1:{server.server_address[1]}"},
            check=True, timeout=100,
        )
        assert len(calls) == 1
        review = store.property("p1")["property"]["review"]
        assert review["status"] == "approved" and review["target_fit"] == "unsure"
        assert store.property("p1")["images"][0]["review"]["status"] == "unreviewed"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        if studio.assessments.thread:
            studio.assessments.thread.join(5)
print("Verified isolated real-browser workflow; no paid calls or live labels changed.")
