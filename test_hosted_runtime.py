"""Run with stdlib unittest in a venv containing ONLY requirements-hosted.txt."""
from contextlib import contextmanager
from copy import deepcopy
import http.client
from http.server import ThreadingHTTPServer
import importlib.abc
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode


ML_PACKAGES = {"numpy", "scipy", "sklearn", "torch", "transformers", "joblib"}


class NoTrainingImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in ML_PACKAGES:
            raise ModuleNotFoundError(f"Hosted review must not import {fullname}", name=fullname)
        return None


class MemoryDatabase:
    workspace = "11111111-1111-1111-1111-111111111111"

    def __init__(self):
        self.states = {}
        self.events = []
        self.rows = []

    @contextmanager
    def connect(self):
        previous = deepcopy((self.states, self.events))
        try:
            yield self
        except Exception:
            self.states, self.events = previous
            raise

    def state(self, db, kind, identifier):
        return deepcopy(self.states.get((kind, identifier)))

    def save(self, db, kind, identifier, expected, payload):
        current = self.states.get((kind, identifier), {})
        if current.get("revision", 0) != expected:
            raise RuntimeError("Review changed; reload before saving")
        result = {**payload, "revision": expected + 1}
        self.states[kind, identifier] = deepcopy(result)
        return result

    def execute(self, sql, params):
        self.rows = []
        if "INSERT INTO acq_training.review_events" in sql:
            self.events.append(params)
        elif "SELECT DISTINCT ON (e.listing_key)" in sql:
            self.rows = [{"listing_key": "house", "group_id": "synthetic-group",
                          "protected_test": True, "listing": self.listing, "photo_count": 1}]
        elif "FROM acq_training.studio_state" in sql or "FROM acq_training.review_events" in sql:
            pass
        elif "pg_advisory_xact_lock" in sql:
            pass
        else:
            raise AssertionError("Unexpected SQL in hosted runtime fixture")
        return self

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class HostedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.prior_modules = set(sys.modules)
        self.blocker = NoTrainingImports()
        sys.meta_path.insert(0, self.blocker)
        self.addCleanup(sys.meta_path.remove, self.blocker)
        self.env = patch.dict(os.environ, {
            "STUDIO_PUBLIC_ORIGIN": "https://studio.example.test",
            "STUDIO_LOGIN_USERNAME": "reviewer@example.test",
            "STUDIO_LOGIN_PASSWORD": "synthetic-hosted-test-password",
            "STUDIO_SESSION_SECRET": "s" * 32, "STUDIO_ROLE": "reviewer",
            "STUDIO_AUTOLABEL_PROVIDER": "hybrid", "OPENAI_API_KEY": "",
            "ACTVISION_SERVICE_TOKEN": "t" * 32,
            "ACTVISION_SOURCE_WORKSPACE_ID": MemoryDatabase.workspace,
            "ACTVISION_RELEASE_MANIFEST": "",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        from PIL import Image
        from cloud_runtime import CloudApp
        from cloud_store import SupabaseStore
        from hosted_server import HostedAuth, create_server
        from openai_labels import start_hosted_worker

        self.folder = tempfile.TemporaryDirectory(prefix="hosted-smoke-")
        self.addCleanup(self.folder.cleanup)
        photo = Path(self.folder.name) / "photo.png"
        Image.new("RGB", (20, 20)).save(photo)
        candidate = {
            "listing": {
                "ListingKey": "house", "ListingId": "SYN-1", "StandardStatus": "Closed",
                "ListDate": "2026-01-01", "UnparsedAddress": "Synthetic property",
                "PublicRemarks": "Bring your vision.", "YearBuilt": 1963,
                "PriorSales": [{"date": "2020-01-01", "price": 100000},
                               {"date": "2026-02-01", "price": 200000}],
            },
            "match": {"exact_apn": True, "street_number_matches": True, "unit_conflict": False,
                      "sale_agreements": [{"source_sale": "prior", "price_agrees": True,
                                           "minimum_date_gap_days": 1}]},
        }

        class SyntheticStore(SupabaseStore):
            # Only storage is synthetic; property assembly, history, policy and saves are real.
            def _examples(self, db, identifier):
                if identifier != "house":
                    raise ValueError("Unknown property")
                return [{
                    "id": "22222222-2222-2222-2222-222222222222", "listing_key": "house",
                    "source_rows": [1], "source_snapshot": {"mls_candidates": [candidate], "spreadsheet": {}},
                }]

            def _photos(self, db, identifier):
                return [{"image_id": "house:photo", "image_sha256": "a" * 64,
                         "protected_test": True, "context": "subject_interior",
                         "context_evidence": {"provider_metadata": {"Order": 1}}}]

            def _legacy(self, db, identifiers, fields=None):
                return {}

            def _reviews(self, db, identifiers):
                return {key: deepcopy(value) for key, value in db.states.items() if key[1] in identifiers}

            def image_path(self, identifier):
                if identifier != "house:photo":
                    raise ValueError("Unknown photo")
                return photo

        self.database = MemoryDatabase()
        self.database.listing = candidate["listing"]
        self.store = SyntheticStore(self.database, None)
        self.app = CloudApp(self.store)
        self.assertIsNone(start_hosted_worker(self.store))
        self.assertEqual(self.store.document("autolabel-worker")["status"], "unconfigured")
        with patch("hosted_server.ThreadingHTTPServer",
                   lambda address, handler: ThreadingHTTPServer(("127.0.0.1", address[1]), handler)):
            self.server = create_server(0, self.app, HostedAuth())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.cookie = None
        status, headers, _ = self.request("POST", "/login", urlencode({
            "username": "reviewer@example.test", "password": "synthetic-hosted-test-password",
        }), {"Content-Type": "application/x-www-form-urlencoded", "Origin": "https://studio.example.test"})
        self.assertEqual(status, 303)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        supplied = {"Host": "studio.example.test"}
        if self.cookie:
            supplied["Cookie"] = self.cookie
        supplied.update(headers or {})
        if isinstance(body, dict):
            body = json.dumps(body)
            supplied.update({"Content-Type": "application/json", "Origin": "https://studio.example.test",
                             "X-Review-Token": self.app.token})
        try:
            connection.request(method, path, body, supplied)
            response = connection.getresponse()
            data = response.read()
            if "application/json" in response.getheader("Content-Type", ""):
                data = json.loads(data)
            return response.status, dict(response.getheaders()), data
        finally:
            connection.close()

    def test_authenticated_workbench_property_open_without_ml_stack(self):
        status, _, queue = self.request("GET", "/api/studio/workbench/properties")
        self.assertEqual(status, 200)
        self.assertEqual(queue["items"][0]["id"], "house")
        status, _, detail = self.request("GET", "/api/studio/workbench/property?id=house")
        self.assertEqual(status, 200)
        self.assertEqual(detail["property"]["id"], "house")
        self.assertEqual(detail["v1_metadata"]["PriorSaleCount"], 1)
        self.assertEqual(detail["v1_metadata"]["MostRecentPriorSalePrice"], 100000)
        self.assertEqual(len(detail["images"]), 1)
        self.assertIsNone(detail["result"])
        self.assertIsNone(detail["feedback"])
        self.assertNotIn("v1_models", set(sys.modules) - self.prior_modules)

    def test_startup_property_label_and_adjacent_read_endpoints(self):
        for path in ("/", "/workbench", "/property-review", "/health",
                     "/api/studio/v2/capabilities", "/api/studio/workbench/summary",
                     "/api/studio/workbench/candidates", "/api/studio/v2/operations",
                     "/api/studio/property?id=house", "/api/studio/v2/property?id=house"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 200)
        status, _, detail = self.request("GET", "/api/studio/v2/property?id=house")
        label = {
            "id": "house", "expected_revision": 0, "label_evidence_id": detail["label_evidence_id"],
            "status": "approved", "physical_condition": "UNKNOWN", "modernization_state": "UNKNOWN",
            "target_fit": "unsure", "condition_label": "unknown", "reason": "Insufficient evidence",
            "text_signals": [{"signal": "clear_slate_or_blank_canvas", "state": "PRESENT",
                              "probability": None, "snippet": "Bring your vision", "start": 0, "end": 17}],
        }
        status, _, saved = self.request("POST", "/api/studio/v2/label", label)
        self.assertEqual(status, 200)
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(saved["target_fit"], "unsure")
        self.assertEqual(len(self.database.events), 4)
        self.assertEqual(self.request("POST", "/api/studio/v2/label", label)[0], 409)
        self.assertEqual(self.request("POST", "/api/studio/v2/train", {"confirmed": True})[0], 403)
        self.assertFalse(any(key[1].startswith("workbench-training:") for key in self.database.states))
        from PIL import Image
        for path in ("/api/studio/image?id=house%3Aphoto", "/api/studio/thumbnail?id=house%3Aphoto"):
            status, _, blob = self.request("GET", path)
            self.assertEqual(status, 200)
            with Image.open(io.BytesIO(blob)) as image:
                self.assertEqual(image.size, (20, 20))


class AcquisitionMetadataTests(unittest.TestCase):
    def test_cutoff_aliases_and_source_transactions_preserve_acquisition_boundary(self):
        from acquisition_metadata import acquisition_time_metadata
        for key in ("ListDate", "OnMarketDate", "ListingContractDate", "listed_at"):
            with self.subTest(key=key):
                original = {key: "2026-01-01"}
                result = acquisition_time_metadata(original, {
                    "Prior Sale Date": "2020-01-01", "Prior Sale Amount": "100000",
                    "Last Sale Date": "2026-02-01", "Last Sale Amount": "200000",
                })
                self.assertEqual(result["PriorSaleCount"], 1)
                self.assertEqual(result["MostRecentPriorSalePrice"], 100000)
                self.assertGreater(result["MonthsSinceMostRecentPriorSale"], 70)
                self.assertEqual(original, {key: "2026-01-01"})

    def test_pinned_counts_and_unknown_dates_keep_legacy_behavior(self):
        from acquisition_metadata import acquisition_time_metadata
        pinned = {"PriorSaleCount": 2, "MostRecentPriorSalePrice": 123}
        self.assertEqual(acquisition_time_metadata(pinned), pinned)
        for metadata in (None, {"ListDate": "invalid"}, {"ListDate": 20260101}):
            self.assertEqual(acquisition_time_metadata(metadata)["PriorSaleCount"], 0)

    def test_explicit_sales_exclude_listing_day_and_future_without_source_fallback(self):
        from acquisition_metadata import acquisition_time_metadata
        result = acquisition_time_metadata({
            "ListDate": "2026-01-01",
            "PriorSales": [
                {"date": "2020-01-01", "price": "bad-price"},
                {"date": "2026-01-01", "price": 200000},
                {"date": "2027-01-01", "price": 300000},
                {"date": "invalid", "price": 1}, None,
            ],
        }, {"Prior Sale Date": "2021-01-01", "Prior Sale Amount": 999})
        self.assertEqual(result["PriorSaleCount"], 1)
        self.assertNotIn("MostRecentPriorSalePrice", result)


if __name__ == "__main__":
    installed = [name for name in ML_PACKAGES if importlib.util.find_spec(name) is not None]
    if installed:
        raise SystemExit("Use a clean requirements-hosted.txt-only venv; unexpected packages: " + ", ".join(sorted(installed)))
    unittest.main()
