"""Hosted property detail must work without model-training dependencies."""
import copy
import importlib.abc
import json
import subprocess
import sys
from pathlib import Path

from acquisition_metadata import acquisition_time_metadata


def test_acquisition_metadata_excludes_sales_at_or_after_listing():
    metadata = {"ListDate": "2026-06-01", "PriorSales": [
        {"date": "2020-01-15", "price": 350000},
        {"date": "2026-06-01", "price": 500000},
        {"date": "2026-07-01", "price": 600000},
    ]}
    original = copy.deepcopy(metadata)
    result = acquisition_time_metadata(metadata)
    assert metadata == original
    assert result["PriorSaleCount"] == 1
    assert result["MostRecentPriorSalePrice"] == 350000
    assert result["MonthsSinceMostRecentPriorSale"] > 70
    assert acquisition_time_metadata({"PriorSaleCount": 2}) == {"PriorSaleCount": 2}
    assert acquisition_time_metadata({})["PriorSaleCount"] == 0


def test_hosted_property_detail_without_training_imports():
    script = r'''
import importlib.abc
import json
import sys

class NoTrainingImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"numpy", "scipy", "sklearn", "torch", "v1_models"}:
            raise ModuleNotFoundError("Training dependency forbidden: " + fullname)
sys.meta_path.insert(0, NoTrainingImports())

from model_workbench import property_detail

class Database:
    def connect(self): return self
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, *args): return self
    def fetchone(self): return None

class Store:
    workspace = "isolated-fixture"
    database = Database()
    def property(self, identifier):
        return {
            "property": {"id": identifier, "metadata": {"ListDate": "2026-06-01"}},
            "historical_source": {"source": {
                "Prior Sale Date": "2020-01-15", "Prior Sale Amount": 350000,
                "Last Sale Date": "2026-07-01", "Last Sale Amount": 600000,
            }, "photo_coverage": "unknown"},
            "images": [{"id": "photo-1", "effective": {"room": "kitchen"},
                        "selection": {"included": False}, "sequence": 1}],
        }

result = property_detail(Store(), "fixture-property")
assert result["v1_metadata"]["PriorSaleCount"] == 1
assert result["v1_metadata"]["MostRecentPriorSalePrice"] == 350000
assert result["images"][0]["included"] is False
assert result["images"][0]["room"] == "kitchen"
assert result["coverage"] == "unknown"
assert len(result["transaction_history"]) == 2
assert result["feedback"] is None and result["result"] is None
assert not any(name in sys.modules for name in ("v1_models", "numpy", "sklearn", "torch"))
print(json.dumps({"property_detail": "ok", "training_imports": "none"}))
'''
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).parent,
        capture_output=True, text=True, check=True,
    )
    assert json.loads(completed.stdout)["property_detail"] == "ok"
